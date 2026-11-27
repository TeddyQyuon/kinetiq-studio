"""Consume persisted i2v completions even when the orchestrator runs on another host."""

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
import logging

from sqlalchemy import event, inspect, or_, update

from app.core.db import SessionLocal
from app.models import Job, Scene, TimelineRevision
from app.qa.service import CLIP_MODELS, evaluate_completed_clip, pending_clip_jobs


@event.listens_for(SessionLocal, "before_flush")
def gate_completed_clip_revision(db, _context, _instances):
    """Keep i2v in QA in the same revision/transaction that publishes its assets.

    Worker completion saves the revision and scene projection together. Scope this
    hook to that specific succeeded transition, leaving ordinary editor saves alone.
    """
    revisions = {row.project_id: row for row in db.new if isinstance(row, TimelineRevision)}
    if not revisions:
        return
    for job in db.dirty:
        if not isinstance(job, Job) or job.job_type not in CLIP_MODELS or job.project_id not in revisions:
            continue
        if "succeeded" not in inspect(job).attrs.status.history.added:
            continue
        revision = revisions[job.project_id]
        scene = next((s for s in revision.document["tracks"]["visual"] if s["id"] == job.scene_id), None)
        if scene and scene["kind"] == "ai_video":
            scene["status"] = "qa_checking"
            scene["qa"] = {
                "score": None,
                "attempts": 0,
                "verdict": "pending",
                "reason": f"Awaiting clip QA for render {job.id}",
            }
            projection = db.get(Scene, job.scene_id)
            if projection:
                projection.status = "qa_checking"
                projection.qa_score = None
                projection.qa_attempts = 0


async def consume_clip_completions():
    while True:
        try:
            now = datetime.now(timezone.utc)
            available = or_(Job.lease_until.is_(None), Job.lease_until < now)
            with SessionLocal() as db:
                candidate = (
                    pending_clip_jobs(db)
                    .filter(available)
                    .order_by(Job.updated_at, Job.id)
                    .with_entities(Job.id)
                    .first()
                )
                if candidate:
                    # The renderer releases this lease on success. Reuse it to avoid duplicate
                    # model calls across API replicas and recover abandoned work after restart.
                    claim = db.execute(
                        update(Job)
                        .where(
                            Job.id == candidate.id,
                            available,
                            Job.id.in_(pending_clip_jobs(db).with_entities(Job.id).statement),
                        )
                        .values(lease_until=now + timedelta(minutes=10))
                    )
                    db.commit()
                    if claim.rowcount:
                        await evaluate_completed_clip(db, db.get(Job, candidate.id))
                    await asyncio.sleep(0)
                    continue
        except Exception:
            logging.getLogger(__name__).exception("Clip QA consumer failed")
        await asyncio.sleep(2)


@asynccontextmanager
async def clip_qa_lifespan(_app):
    task = asyncio.create_task(consume_clip_completions())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

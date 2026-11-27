from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal, init_db
from app.core.logging import configure_logging
from app.events.service import emit_event
from app.models import Job, Project, Scene, Worker

logger = logging.getLogger("kinetiq.orchestrator")


def run_orchestrator_tick(db: Session) -> dict[str, int]:
    settings = get_settings()
    now = datetime.now(timezone.utc)

    # 1. Reclaim expired job leases (Colab disconnect recovery)
    expired = (
        db.query(Job)
        .filter(Job.status.in_(["leased", "running"]), Job.lease_until.is_not(None), Job.lease_until < now)
        .all()
    )
    reclaimed = 0
    for job in expired:
        job.worker_id = None
        job.lease_until = None
        if job.attempts < job.max_attempts:
            job.status = "queued"
            job.next_run_at = now
        else:
            job.status = "dead"
            job.error_message = "Lease expired repeatedly across max attempts"
        emit_event(
            db,
            "job.lease_expired",
            {"job_id": job.id, "scene_id": job.scene_id, "new_status": job.status},
            project_id=job.project_id,
        )
        reclaimed += 1

    # 2. Mark stale workers offline
    cutoff = now - timedelta(seconds=settings.worker_offline_after_seconds)
    stale_workers = (
        db.query(Worker)
        .filter(Worker.status != "offline", Worker.last_heartbeat.is_not(None), Worker.last_heartbeat < cutoff)
        .all()
    )
    marked_offline = 0
    for w in stale_workers:
        w.status = "offline"
        emit_event(db, "worker.offline", {"worker_id": w.id, "name": w.name})
        marked_offline += 1

    # 3. Advance project status when all scenes reach ready
    generating_projects = db.query(Project).filter(Project.status == "generating").all()
    completed_projects = 0
    for proj in generating_projects:
        scenes = db.query(Scene).filter(Scene.project_id == proj.id).all()
        if scenes and all(s.status == "ready" for s in scenes):
            proj.status = "ready"
            emit_event(db, "project.ready", {"project_id": proj.id}, project_id=proj.id)
            completed_projects += 1

    if reclaimed or marked_offline or completed_projects:
        db.commit()

    return {
        "reclaimed_jobs": reclaimed,
        "offline_workers": marked_offline,
        "completed_projects": completed_projects,
    }


async def orchestrator_loop(interval_seconds: int = 30) -> None:
    logger.info("Orchestrator loop started (interval=%ss)", interval_seconds)
    while True:
        try:
            with SessionLocal() as db:
                stats = run_orchestrator_tick(db)
                if any(stats.values()):
                    logger.info("Orchestrator tick: %s", stats)
        except Exception as exc:
            logger.exception("Orchestrator tick failed: %s", exc)
        await asyncio.sleep(interval_seconds)


if __name__ == "__main__":
    configure_logging()
    init_db()
    asyncio.run(orchestrator_loop(15))

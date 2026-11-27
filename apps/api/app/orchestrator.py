from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal, init_db
from app.core.errors import NotFoundError, ValidationFailedError
from app.core.logging import configure_logging
from app.events.service import emit_event
from app.exports.router import queue_export
from app.models import Export, Job, Project, Scene, Worker
from app.storage.reconcile import reconcile_vault
from app.storage.snapshots import snapshot_pending_revisions

logger = logging.getLogger("kinetiq.orchestrator")


def _queue_draft_if_missing(db: Session, project: Project) -> None:
    existing = select(Export.id).where(Export.project_id == project.id, Export.revision == project.current_revision)
    if db.scalar(existing.limit(1)) is not None:
        return
    try:
        # A later scene can fail validation after earlier scenes queued dependencies.
        with db.begin_nested():
            queue_export(db, project, "1080p")
    except (ValidationFailedError, ValidationError, NotFoundError):
        logger.warning(
            "Skipping draft export for project %s revision %s: incomplete inputs", project.id, project.current_revision
        )


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
    ready = (Project.status == "generating", Project.scenes.any(), ~Project.scenes.any(Scene.status != "ready"))
    completed_projects = 0
    for proj in db.scalars(select(Project).where(*ready).order_by(Project.id)).all():
        # Only the tick winning this transition may enqueue a draft for this revision.
        changed = db.execute(
            update(Project)
            .where(Project.id == proj.id, Project.current_revision == proj.current_revision, *ready)
            .values(status="ready")
        )
        if changed.rowcount == 1:
            emit_event(db, "project.ready", {"project_id": proj.id}, project_id=proj.id)
            if settings.auto_export_draft_on_ready:
                _queue_draft_if_missing(db, proj)
            completed_projects += 1

    if reclaimed or marked_offline or completed_projects:
        db.commit()

    return {
        "reclaimed_jobs": reclaimed,
        "offline_workers": marked_offline,
        "completed_projects": completed_projects,
    }


async def _reconciliation_loop() -> None:
    while True:
        for operation in (snapshot_pending_revisions, reconcile_vault):
            try:
                with SessionLocal() as db:
                    await operation(db)
            except Exception:
                logger.exception("Vault operation %s failed", operation.__name__)
        await asyncio.sleep(get_settings().vault_reconcile_interval_seconds)


async def orchestrator_loop(interval_seconds: int = 30) -> None:
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(_reconciliation_loop())
        tasks.create_task(_lease_loop(interval_seconds))


async def _lease_loop(interval_seconds: int) -> None:
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

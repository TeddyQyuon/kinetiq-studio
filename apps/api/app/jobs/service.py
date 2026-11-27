from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.events.service import emit_event
from app.models import Job, Worker

RETRY_BACKOFF_SECONDS = [30, 120, 480]


def estimate_colab_cu(gpu_model: str | None, gpu_seconds: float) -> float:
    name = (gpu_model or "").upper()
    if "H100" in name:
        rate_per_hr = 16.0
    elif "A100" in name:
        rate_per_hr = 6.8
    elif "L4" in name:
        rate_per_hr = 1.65
    elif "T4" in name:
        rate_per_hr = 1.1
    else:
        rate_per_hr = 1.65
    return round((max(0.0, gpu_seconds) / 3600.0) * rate_per_hr, 4)


def compute_idempotency_key(project_id: str, scene_id: str | None, job_type: str, payload: dict[str, Any]) -> str:
    raw = json.dumps(
        {"project_id": project_id, "scene_id": scene_id, "job_type": job_type, "payload": payload},
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def enqueue_job(
    db: Session,
    *,
    project_id: str,
    scene_id: str | None,
    job_type: str,
    capability: str,
    payload: dict[str, Any],
    depends_on: list[str] | None = None,
    priority: int = 5,
    est_cu: float = 0.0,
    salt: str | None = None,
) -> Job:
    key_payload = {**payload, **({"_salt": salt} if salt else {})}
    idem_key = compute_idempotency_key(project_id, scene_id, job_type, key_payload)

    existing = db.query(Job).filter(Job.idempotency_key == idem_key).first()
    if existing and existing.status != "dead":
        return existing

    job = Job(
        project_id=project_id,
        scene_id=scene_id,
        job_type=job_type,
        capability=capability,
        status="queued",
        priority=priority,
        idempotency_key=idem_key,
        depends_on=depends_on or [],
        payload=payload,
        est_cu=est_cu,
    )
    db.add(job)
    db.flush()
    emit_event(
        db,
        "job.queued",
        {"job_id": job.id, "job_type": job_type, "scene_id": scene_id, "capability": capability},
        project_id=project_id,
    )
    return job


def _dependencies_met(db: Session, dep_ids: list[str]) -> bool:
    if not dep_ids:
        return True
    rows = db.query(Job.status).filter(Job.id.in_(dep_ids)).all()
    if len(rows) < len(dep_ids):
        return False
    return all(r[0] == "succeeded" for r in rows)


def claim_next_job(db: Session, worker: Worker, capabilities: list[str]) -> Job | None:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    lease_until = now + timedelta(seconds=settings.job_lease_seconds)

    is_postgres = db.bind is not None and db.bind.dialect.name == "postgresql"
    query = (
        db.query(Job)
        .filter(
            Job.status == "queued",
            Job.capability.in_(capabilities),
            (Job.next_run_at.is_(None)) | (Job.next_run_at <= now),
        )
        .order_by(Job.priority.asc(), Job.created_at.asc())
        .limit(20)
    )
    if is_postgres:
        query = query.with_for_update(skip_locked=True)

    candidates = query.all()
    for candidate in candidates:
        if not _dependencies_met(db, candidate.depends_on or []):
            continue

        if is_postgres:
            candidate.status = "leased"
            candidate.worker_id = worker.id
            candidate.lease_until = lease_until
            candidate.attempts += 1
            db.commit()
            db.refresh(candidate)
            return candidate
        else:
            # Atomic compare-and-set on SQLite
            stmt = (
                update(Job)
                .where(Job.id == candidate.id, Job.status == "queued")
                .values(
                    status="leased",
                    worker_id=worker.id,
                    lease_until=lease_until,
                    attempts=Job.attempts + 1,
                    updated_at=now,
                )
            )
            res = db.execute(stmt)
            if res.rowcount == 1:
                db.commit()
                db.refresh(candidate)
                return candidate
            db.rollback()
    return None


def renew_job_lease(db: Session, job: Job, progress_pct: int) -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    job.status = "running"
    job.progress_pct = max(0, min(100, progress_pct))
    job.lease_until = now + timedelta(seconds=settings.job_lease_seconds)
    emit_event(
        db,
        "job.progress",
        {
            "job_id": job.id,
            "scene_id": job.scene_id,
            "job_type": job.job_type,
            "progress_pct": job.progress_pct,
        },
        project_id=job.project_id,
    )
    db.commit()


def fail_job(db: Session, job: Job, error_message: str) -> None:
    now = datetime.now(timezone.utc)
    job.error_message = error_message[:2000]
    job.worker_id = None
    job.lease_until = None

    if job.attempts < job.max_attempts:
        delay = RETRY_BACKOFF_SECONDS[min(job.attempts - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
        job.status = "queued"
        job.next_run_at = now + timedelta(seconds=delay)
    else:
        job.status = "dead"

    emit_event(
        db,
        "job.failed",
        {
            "job_id": job.id,
            "scene_id": job.scene_id,
            "status": job.status,
            "error": job.error_message,
        },
        project_id=job.project_id,
    )
    db.commit()

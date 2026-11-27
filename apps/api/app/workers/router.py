from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import generate_token, hash_token
from app.core.db import get_db
from app.core.errors import ConflictError, NotFoundError, UnauthorizedError, ValidationFailedError
from app.events.service import emit_event
from app.exports.service import finish_export
from app.identity.service import ensure_default_owner, get_valid_google_access_token
from app.jobs.service import claim_next_job, enqueue_job, estimate_colab_cu, fail_job, renew_job_lease
from app.models import Asset, Job, Project, Worker
from app.qa.service import evaluate_keyframe_with_hotl
from app.storage.drive import get_storage_provider
from app.timeline.schemas import SceneAssetRefs, SceneQASummary
from app.timeline.service import get_latest_timeline, save_timeline_document
from app.workers.claims import UPSCALE_JOBS, VIDEO_JOBS, build_claim
from app.workers.schemas import (
    JobClaimRequest,
    JobClaimResponse,
    JobCompleteRequest,
    JobFailRequest,
    JobProgressRequest,
    UploadSessionRequest,
    UploadSessionResponse,
    WorkerHeartbeatRequest,
    WorkerRead,
    WorkerRegisterRequest,
    WorkerRegisterResponse,
    WorkerTokenCreateRequest,
    WorkerTokenCreateResponse,
)
from app.workers.workflows import PROFILE_CAPABILITIES

router = APIRouter(prefix="/v1/workers", tags=["workers"])


def _auth_worker(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Worker:
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedError("Missing Worker Bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    worker = db.query(Worker).filter(Worker.token_hash == hash_token(token)).first()
    if not worker:
        raise UnauthorizedError("Invalid or revoked Worker token")
    return worker


@router.get("", response_model=list[WorkerRead])
def list_workers(db: Session = Depends(get_db)) -> list[WorkerRead]:
    rows = db.query(Worker).order_by(Worker.created_at.desc()).all()
    return [
        WorkerRead(
            id=w.id,
            name=w.name,
            provider=w.provider,
            profile=w.profile,
            gpu_model=w.gpu_model,
            vram_gb=w.vram_gb,
            capabilities=w.capabilities or [],
            status=w.status,
            last_heartbeat=w.last_heartbeat,
            cu_used_month=w.cu_used_month,
        )
        for w in rows
    ]


@router.post("/tokens", response_model=WorkerTokenCreateResponse)
def create_worker_token(body: WorkerTokenCreateRequest, db: Session = Depends(get_db)) -> WorkerTokenCreateResponse:
    raw_token = generate_token("wrk")
    caps = PROFILE_CAPABILITIES.get(body.profile, ["keyframe_qwen", "i2v_ltx25"])
    worker = Worker(
        name=body.name,
        provider=body.provider,
        profile=body.profile,
        capabilities=caps,
        token_hash=hash_token(raw_token),
        status="offline",
    )
    db.add(worker)
    db.commit()
    db.refresh(worker)
    return WorkerTokenCreateResponse(
        worker_id=worker.id,
        name=worker.name,
        profile=worker.profile,
        worker_token=raw_token,
        capabilities=caps,
    )


@router.delete("/{worker_id}")
def delete_worker(worker_id: str, db: Session = Depends(get_db)) -> dict[str, bool]:
    worker = db.query(Worker).filter(Worker.id == worker_id).first()
    if not worker:
        raise NotFoundError(f"Worker {worker_id} not found")
    db.delete(worker)
    db.commit()
    return {"ok": True}


@router.post("/register", response_model=WorkerRegisterResponse)
def register_worker(
    body: WorkerRegisterRequest,
    worker: Worker = Depends(_auth_worker),
    db: Session = Depends(get_db),
) -> WorkerRegisterResponse:
    worker.provider = body.provider
    worker.profile = body.profile
    worker.gpu_model = body.gpu_model
    worker.vram_gb = body.vram_gb
    worker.capabilities = body.capabilities or PROFILE_CAPABILITIES.get(body.profile, [])
    worker.status = "online"
    worker.last_heartbeat = datetime.now(timezone.utc)
    emit_event(
        db,
        "worker.online",
        {"worker_id": worker.id, "name": worker.name, "gpu_model": worker.gpu_model, "vram_gb": worker.vram_gb},
    )
    db.commit()
    return WorkerRegisterResponse(worker_id=worker.id, name=worker.name, capabilities=worker.capabilities)


@router.post("/heartbeat")
def heartbeat(
    body: WorkerHeartbeatRequest,
    worker: Worker = Depends(_auth_worker),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    worker.last_heartbeat = datetime.now(timezone.utc)
    if worker.status == "offline":
        worker.status = "online"
    db.commit()
    return {"ok": True}


@router.post("/jobs/claim", response_model=JobClaimResponse, responses={204: {"description": "No job available"}})
def claim_job(
    body: JobClaimRequest,
    worker: Worker = Depends(_auth_worker),
    db: Session = Depends(get_db),
):
    caps = body.capabilities or worker.capabilities or []
    job = claim_next_job(db, worker, caps)
    if not job:
        return Response(status_code=204)
    try:
        claim = build_claim(db, job, worker)
    except (NotFoundError, ValidationFailedError) as exc:
        # A bad input belongs to this job. Keep the worker polling other jobs.
        fail_job(db, job, str(exc))
        return Response(status_code=204)
    worker.status = "busy"
    worker.last_heartbeat = datetime.now(timezone.utc)
    db.commit()
    return claim


def _owned_job(db: Session, job_id: str, worker: Worker, *, allow_completed: bool = False) -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise NotFoundError(f"Job {job_id} not found")
    if job.worker_id != worker.id or (
        job.status not in {"leased", "running"} and not (allow_completed and job.status == "succeeded")
    ):
        raise ConflictError("This worker no longer holds the job lease")
    return job


def _lock_lease(db: Session, job: Job, worker: Worker, *, allow_completed: bool = False) -> bool:
    # A conditional write serializes renewal, failure and completion with reassignment
    # on SQLite as well as PostgreSQL. The caller holds the lock until commit.
    held = db.execute(
        update(Job)
        .where(
            Job.id == job.id,
            Job.worker_id == worker.id,
            Job.attempts == job.attempts,
            Job.status.in_(["leased", "running"]),
            Job.lease_until > datetime.now(timezone.utc),
        )
        .values(status="running")
        .execution_options(synchronize_session=False)
    )
    if held.rowcount == 1:
        return True
    db.refresh(job)
    if allow_completed and job.status == "succeeded" and job.worker_id == worker.id:
        return False
    raise ConflictError("Job lease expired or changed")


@router.post("/jobs/{job_id}/progress")
def report_progress(
    job_id: str,
    body: JobProgressRequest,
    worker: Worker = Depends(_auth_worker),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    job = _owned_job(db, job_id, worker)
    _lock_lease(db, job, worker)
    worker.last_heartbeat = datetime.now(timezone.utc)
    renew_job_lease(db, job, job.progress_pct if body.percent is None else body.percent)
    return {"ok": True}


SCOPED_JOBS = {"render", "tts", "align", "transcode", "upscale_seedvr2"}


def _upload_prefix(job: Job, kind: str) -> str:
    subdir = {
        "keyframe": "keyframes",
        "master": "clips/master",
        "proxy": "clips/proxy",
        "upscaled_4k": "clips/master",
        "audio": "audio",
        "export": "exports",
        "srt": "exports",
        "words": "audio",
    }.get(kind, "clips/master")
    if job.job_type in SCOPED_JOBS:
        subdir = f"{subdir}/{job.id}/{job.attempts}"
    return f"{job.project_id}/{subdir}/"


@router.post("/jobs/{job_id}/upload-session", response_model=UploadSessionResponse)
async def create_upload_session(
    job_id: str,
    body: UploadSessionRequest,
    worker: Worker = Depends(_auth_worker),
    db: Session = Depends(get_db),
) -> UploadSessionResponse:
    job = _owned_job(db, job_id, worker)
    project = db.query(Project).filter(Project.id == job.project_id).first()
    if not project:
        raise NotFoundError(f"Project {job.project_id} not found")

    user = ensure_default_owner(db)
    provider = get_storage_provider(db)
    slug = "".join(c if c.isalnum() else "-" for c in project.title.lower()).strip("-")[:32] or "project"
    if provider.name == "drive":
        folder_id = project.drive_folder_id
        if not folder_id:
            folder_id = await provider.ensure_project_space(project.id, slug, user.id)
            project.drive_folder_id = folder_id
            db.commit()
    else:
        folder_id = await provider.ensure_project_space(project.id, slug, user.id)

    storage_key = _upload_prefix(job, body.asset_kind) + body.filename

    info = await provider.create_upload_session(
        user_id=user.id,
        project_id=project.id,
        project_folder_id=folder_id,
        storage_key=storage_key,
        filename=body.filename,
        content_type=body.content_type,
        total_bytes=body.total_bytes,
        app_properties={
            "project_id": project.id,
            "scene_id": job.scene_id or "",
            "kind": body.asset_kind,
            "revision": str(job.payload.get("revision", project.current_revision)),
        },
    )
    refresh_url = info.token_refresh_url.format(job_id=job.id) if info.token_refresh_url else None
    return UploadSessionResponse(
        provider=info.provider,
        storage_key=info.storage_key,
        session_uri=info.session_uri,
        upload_headers=info.upload_headers,
        chunk_size_bytes=info.chunk_size_bytes,
        token_refresh_url=refresh_url,
    )


@router.post("/jobs/{job_id}/upload-token-refresh")
async def refresh_upload_token(
    job_id: str,
    worker: Worker = Depends(_auth_worker),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    _owned_job(db, job_id, worker)
    settings = get_settings()
    if settings.storage_provider != "drive":
        return {"authorization": ""}
    user = ensure_default_owner(db)
    token = await get_valid_google_access_token(db, user.id)
    return {"authorization": f"Bearer {token}"}


@router.post("/jobs/{job_id}/complete")
async def complete_job(
    job_id: str,
    body: JobCompleteRequest,
    worker: Worker = Depends(_auth_worker),
    db: Session = Depends(get_db),
) -> dict:
    job = _owned_job(db, job_id, worker, allow_completed=True)

    # Idempotency guard if worker retries /complete
    if job.status == "succeeded":
        return {"ok": True, "already_completed": True}

    project = db.query(Project).filter(Project.id == job.project_id).first()
    if not project:
        raise NotFoundError(f"Project {job.project_id} not found")

    user = ensure_default_owner(db)
    provider = get_storage_provider(db)
    cu = estimate_colab_cu(worker.gpu_model, body.gpu_seconds)

    expected = {"tts": "audio", "align": "words", "transcode": "proxy"}
    kinds = [a.asset_kind for a in body.artifacts]
    if job.job_type in expected and kinds != [expected[job.job_type]]:
        raise ValidationFailedError(f"{job.job_type} requires one {expected[job.job_type]} artifact")
    if job.job_type == "render" and sorted(kinds) != ["export", "srt"]:
        raise ValidationFailedError("A render requires both an MP4 export and an SRT sidecar")

    files = []
    for art in body.artifacts:
        prefix = _upload_prefix(job, art.asset_kind)
        if job.job_type in SCOPED_JOBS and (
            not art.storage_key.startswith(prefix)
            or "/" in art.storage_key[len(prefix):] or "\\" in art.storage_key
            or art.storage_key[len(prefix):] in {"", ".", ".."}
        ):
            raise ValidationFailedError("Artifact does not belong to this upload attempt")
        verified = await provider.verify_uploaded_file(
            user_id=user.id,
            storage_key=art.storage_key,
            drive_file_id=art.drive_file_id,
            expected_size=art.size_bytes,
            expected_sha256=art.sha256,
        )
        files.append((art, verified))

    # Verify uploads before taking the write lock. All asset,
    # export and job updates below commit together.
    if not _lock_lease(db, job, worker, allow_completed=True):
        return {"ok": True, "already_completed": True}

    created_assets: dict[str, Asset] = {}
    for art, verified in files:
        ast = Asset(
            project_id=project.id,
            scene_id=job.scene_id,
            kind=art.asset_kind,
            storage_provider=provider.name,
            storage_key=verified.storage_key,
            drive_file_id=verified.drive_file_id,
            content_type=art.content_type,
            sha256=verified.sha256 or art.sha256,
            size_bytes=verified.size_bytes,
            width=art.width,
            height=art.height,
            duration_ms=art.duration_ms,
            sync_state="synced",
        )
        db.add(ast)
        db.flush()
        created_assets[art.asset_kind] = ast
        emit_event(
            db,
            "asset.synced",
            {"asset_id": ast.id, "kind": ast.kind, "scene_id": job.scene_id, "provider": provider.name},
            project_id=project.id,
        )

    job.status = "succeeded"
    job.progress_pct = 100
    job.gpu_seconds = body.gpu_seconds
    job.est_cu = cu
    job.lease_until = None
    job.result = {**body.result_meta, "asset_ids": {k: v.id for k, v in created_assets.items()}}

    worker.status = "online"
    worker.last_heartbeat = datetime.now(timezone.utc)
    worker.cu_used_month = round((worker.cu_used_month or 0.0) + cu, 4)

    if job.job_type == "render":
        finish_export(db, job, created_assets)
        db.commit()
        return {"ok": True, "cu_charged": cu}
    if job.job_type in {"tts", "align"}:
        # Export jobs refer to the immutable inputs and outputs by job ID.
        db.commit()
        return {"ok": True, "cu_charged": cu}

    # Update TimelineDocument & trigger HOTL QA or next DAG stage
    doc = get_latest_timeline(db, project.id)
    scene_item = next((s for s in doc.tracks.visual if s.id == job.scene_id), None)

    if (scene_item and job.job_type in UPSCALE_JOBS | {"transcode"}
            and job.payload.get("master_asset_id")
            and (not scene_item.assets or scene_item.assets.master != job.payload["master_asset_id"])):
        # A newer master replaced this input. The pinned export can still use its result.
        scene_item = None

    if scene_item:
        refs = scene_item.assets or SceneAssetRefs()
        if "keyframe" in created_assets:
            refs.keyframe = created_assets["keyframe"].id
        if "master" in created_assets:
            refs.master = created_assets["master"].id
        if "proxy" in created_assets:
            refs.proxy = created_assets["proxy"].id
        if "upscaled_4k" in created_assets:
            refs.upscaled_4k = created_assets["upscaled_4k"].id
        scene_item.assets = refs

        if job.job_type == "keyframe_qwen" and "keyframe" in created_assets:
            kf_asset = created_assets["keyframe"]
            img_bytes, _, _ = await provider.read_range(
                user_id=user.id,
                storage_key=kf_asset.storage_key,
                drive_file_id=kf_asset.drive_file_id,
            )
            _review, decision = await evaluate_keyframe_with_hotl(
                db,
                project=project,
                doc=doc,
                scene_id=scene_item.id,
                asset_id=kf_asset.id,
                image_bytes=img_bytes,
            )
            if decision == "pass":
                if scene_item.kind == "ai_video":
                    scene_item.status = "generating_clip"
                    next_cap = "i2v_wan22" if scene_item.model == "wan22_i2v" else "i2v_ltx25"
                    enqueue_job(
                        db,
                        project_id=project.id,
                        scene_id=scene_item.id,
                        job_type=next_cap,
                        capability=next_cap,
                        payload={"keyframe_asset_id": kf_asset.id, "seed": scene_item.seed},
                        depends_on=[job.id],
                        est_cu=0.06,
                    )
                else:
                    scene_item.status = "ready"
            elif decision == "auto_heal_retry":
                scene_item.status = "generating_keyframe"
                enqueue_job(
                    db,
                    project_id=project.id,
                    scene_id=scene_item.id,
                    job_type="keyframe_qwen",
                    capability="keyframe_qwen",
                    payload={"seed": scene_item.seed, "attempt": (scene_item.qa.attempts if scene_item.qa else 1) + 1},
                    est_cu=0.02,
                    salt=f"retry_{scene_item.seed}",
                )
            elif decision == "convert_diagram":
                scene_item.kind = "diagram"
                scene_item.status = "ready"
            else:
                scene_item.status = "needs_review"

        elif job.job_type in VIDEO_JOBS | UPSCALE_JOBS:
            if "master" in created_assets and "proxy" not in created_assets:
                enqueue_job(
                    db, project_id=project.id, scene_id=scene_item.id, job_type="transcode",
                    capability="transcode", payload={"master_asset_id": refs.master}, depends_on=[job.id],
                )
            scene_item.status = "ready"
            if not scene_item.qa or scene_item.qa.verdict == "pending":
                scene_item.qa = SceneQASummary(score=8.5, attempts=1, verdict="passed", reason="Clip rendered")

        save_timeline_document(db, project, doc.revision, doc, author="ai")
    else:
        db.commit()

    return {"ok": True, "cu_charged": cu}


@router.post("/jobs/{job_id}/fail")
def report_fail(
    job_id: str,
    body: JobFailRequest,
    worker: Worker = Depends(_auth_worker),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    job = _owned_job(db, job_id, worker)
    _lock_lease(db, job, worker)
    worker.status = "online"
    worker.last_heartbeat = datetime.now(timezone.utc)
    fail_job(db, job, body.error)
    return {"ok": True}

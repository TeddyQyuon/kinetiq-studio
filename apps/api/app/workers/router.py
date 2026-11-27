from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import generate_token, hash_token, sign_media_token
from app.core.db import get_db
from app.core.errors import NotFoundError, UnauthorizedError
from app.events.service import emit_event
from app.identity.service import ensure_default_owner, get_valid_google_access_token
from app.jobs.service import claim_next_job, enqueue_job, estimate_colab_cu, fail_job, renew_job_lease
from app.models import Asset, Job, Project, Scene, Worker
from app.qa.service import evaluate_keyframe_with_hotl
from app.storage.drive import get_storage_provider
from app.timeline.schemas import SceneAssetRefs, SceneQASummary
from app.timeline.service import get_latest_timeline, save_timeline_document
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
from app.workers.workflows import PROFILE_CAPABILITIES, build_comfy_graph

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
    settings = get_settings()
    caps = body.capabilities or worker.capabilities or []
    job = claim_next_job(db, worker, caps)
    if not job:
        return Response(status_code=204)

    worker.status = "busy"
    worker.last_heartbeat = datetime.now(timezone.utc)
    db.commit()

    doc = get_latest_timeline(db, job.project_id)
    scene = next((s for s in doc.tracks.visual if s.id == job.scene_id), None)

    input_download_url: str | None = None
    input_filename: str | None = None
    if scene and job.job_type in {"i2v_ltx25", "i2v_wan22"} and scene.assets and scene.assets.keyframe:
        tok = sign_media_token(scene.assets.keyframe, ttl_seconds=600)
        input_download_url = f"{settings.public_api_url.rstrip('/')}/v1/media/{scene.assets.keyframe}?t={tok}"
        input_filename = f"{scene.id}_keyframe.png"
    elif scene and job.job_type == "upscale_flashvsr" and scene.assets and scene.assets.master:
        tok = sign_media_token(scene.assets.master, ttl_seconds=600)
        input_download_url = f"{settings.public_api_url.rstrip('/')}/v1/media/{scene.assets.master}?t={tok}"
        input_filename = f"{scene.id}_master.mp4"

    comfy_graph = None
    if scene and job.job_type in {"keyframe_qwen", "i2v_ltx25", "i2v_wan22", "upscale_flashvsr"}:
        comfy_graph = build_comfy_graph(
            job_type=job.job_type,
            scene=scene,
            aspect=doc.format.aspect,
            target_res=doc.format.target,
            style_look=doc.style_bible.look,
            negative_prompt=doc.style_bible.negative,
            input_filename=input_filename,
        )

    return JobClaimResponse(
        id=job.id,
        project_id=job.project_id,
        scene_id=job.scene_id,
        job_type=job.job_type,
        capability=job.capability,
        payload=job.payload,
        input_download_url=input_download_url,
        input_filename=input_filename,
        comfy_graph=comfy_graph,
    )


@router.post("/jobs/{job_id}/progress")
def report_progress(
    job_id: str,
    body: JobProgressRequest,
    worker: Worker = Depends(_auth_worker),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise NotFoundError(f"Job {job_id} not found")
    worker.last_heartbeat = datetime.now(timezone.utc)
    renew_job_lease(db, job, body.percent)
    return {"ok": True}


@router.post("/jobs/{job_id}/upload-session", response_model=UploadSessionResponse)
async def create_upload_session(
    job_id: str,
    body: UploadSessionRequest,
    worker: Worker = Depends(_auth_worker),
    db: Session = Depends(get_db),
) -> UploadSessionResponse:
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise NotFoundError(f"Job {job_id} not found")
    project = db.query(Project).filter(Project.id == job.project_id).first()
    if not project:
        raise NotFoundError(f"Project {job.project_id} not found")

    user = ensure_default_owner(db)
    provider = get_storage_provider(db)
    folder_id = project.drive_folder_id
    if not folder_id:
        slug = "".join(c if c.isalnum() else "-" for c in project.title.lower()).strip("-")[:32] or "project"
        folder_id = await provider.ensure_project_space(project.id, slug, user.id)
        project.drive_folder_id = folder_id
        db.commit()

    subdir = {
        "keyframe": "keyframes",
        "master": "clips/master",
        "proxy": "clips/proxy",
        "upscaled_4k": "clips/master",
        "audio": "audio",
        "export": "exports",
    }.get(body.asset_kind, "clips/master")
    storage_key = f"{project.id}/{subdir}/{body.filename}"

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
            "revision": str(project.current_revision),
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
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise NotFoundError(f"Job {job_id} not found")

    # Idempotency guard if worker retries /complete
    if job.status == "succeeded":
        return {"ok": True, "already_completed": True}

    project = db.query(Project).filter(Project.id == job.project_id).first()
    if not project:
        raise NotFoundError(f"Project {job.project_id} not found")

    user = ensure_default_owner(db)
    provider = get_storage_provider(db)
    cu = estimate_colab_cu(worker.gpu_model, body.gpu_seconds)

    created_assets: dict[str, Asset] = {}
    for art in body.artifacts:
        verified = await provider.verify_uploaded_file(
            user_id=user.id,
            storage_key=art.storage_key,
            drive_file_id=art.drive_file_id,
            expected_size=art.size_bytes,
        )
        ast = Asset(
            project_id=project.id,
            scene_id=job.scene_id,
            kind=art.asset_kind,
            storage_provider=provider.name,
            storage_key=verified.storage_key,
            drive_file_id=verified.drive_file_id,
            content_type=art.content_type,
            sha256=art.sha256 or verified.sha256,
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
    job.result = {"asset_ids": {k: v.id for k, v in created_assets.items()}, **body.result_meta}

    worker.status = "online"
    worker.last_heartbeat = datetime.now(timezone.utc)
    worker.cu_used_month = round((worker.cu_used_month or 0.0) + cu, 4)

    # Update TimelineDocument & trigger HOTL QA or next DAG stage
    doc = get_latest_timeline(db, project.id)
    scene_item = next((s for s in doc.tracks.visual if s.id == job.scene_id), None)

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

        elif job.job_type in {"i2v_ltx25", "i2v_wan22", "upscale_flashvsr"}:
            scene_item.status = "ready"
            if not scene_item.qa or scene_item.qa.verdict == "pending":
                scene_item.qa = SceneQASummary(score=8.5, attempts=1, verdict="passed", reason="Clip rendered")

        save_timeline_document(db, project, project.current_revision, doc, author="ai")
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
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise NotFoundError(f"Job {job_id} not found")
    worker.status = "online"
    worker.last_heartbeat = datetime.now(timezone.utc)
    fail_job(db, job, body.error)
    return {"ok": True}

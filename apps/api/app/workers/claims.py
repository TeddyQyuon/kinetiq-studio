from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import sign_media_token
from app.core.errors import NotFoundError
from app.exports.service import build_render_payload
from app.models import Asset, Job, TimelineRevision, Worker
from app.timeline.schemas import TimelineDocument
from app.timeline.service import get_latest_timeline
from app.workers.schemas import JobClaimResponse
from app.workers.workflows import build_comfy_graph

UPSCALE_JOBS = {"upscale_flashvsr", "upscale_seedvr2"}
VIDEO_JOBS = {"i2v_ltx25", "i2v_wan22"}


def _attach_input(db: Session, claim: JobClaimResponse, asset_id: str, kind: str, suffix: str) -> None:
    asset = db.query(Asset).filter_by(
        project_id=claim.project_id, id=asset_id, kind=kind, sync_state="synced",
    ).one_or_none()
    if asset is None:
        raise NotFoundError(f"Missing synced {kind} input")
    base = get_settings().public_api_url.rstrip("/")
    token = sign_media_token(asset.id, ttl_seconds=600)
    claim.input_download_url = f"{base}/v1/media/{asset.id}?t={token}"
    claim.input_filename = f"{claim.id}_{kind}.{suffix}"


def _align_input(db: Session, job: Job) -> str | None:
    if asset_id := job.payload.get("audio_asset_id"):
        return asset_id
    parent = db.query(Job).filter(
        Job.id.in_(job.depends_on or []), Job.project_id == job.project_id,
        Job.scene_id == job.scene_id, Job.job_type == "tts", Job.status == "succeeded",
    ).one_or_none()
    return (parent.result or {}).get("asset_ids", {}).get("audio") if parent else None


def build_claim(db: Session, job: Job, worker: Worker) -> JobClaimResponse:
    payload = dict(job.payload)
    claim = JobClaimResponse(
        id=job.id, project_id=job.project_id, scene_id=job.scene_id,
        job_type=job.job_type, capability=job.capability, payload=payload,
    )
    if job.job_type == "render":
        claim.payload = build_render_payload(db, job)
        return claim

    # Narration and voice are immutable job inputs and may be reused across
    # exports. The live timeline can change or delete the scene after enqueue.
    audio = _align_input(db, job) if job.job_type == "align" else None
    if "narration" in payload:
        if job.job_type == "tts" and "voice" in payload:
            return claim
        if job.job_type == "align" and audio:
            _attach_input(db, claim, audio, "audio", "wav")
            return claim

    if "revision" in payload:
        revision = db.query(TimelineRevision).filter_by(
            project_id=job.project_id, revision=payload["revision"]
        ).one_or_none()
        if revision is None:
            raise NotFoundError("The job's timeline revision is missing")
        doc = TimelineDocument.model_validate(revision.document)
    else:
        doc = get_latest_timeline(db, job.project_id)
    scene = next((s for s in doc.tracks.visual if s.id == job.scene_id), None)
    if scene is None:
        raise NotFoundError("The job's scene is missing from its timeline")

    asset_id = None
    if job.job_type in {"tts", "align"}:
        payload.setdefault("narration", scene.narration)
        payload.setdefault("voice", doc.voice.model_dump())
    if job.job_type == "align":
        asset_id = audio or next((v.asset for v in doc.tracks.voice if v.scene_id == scene.id), None)
        kind, suffix = "audio", "wav"
    elif job.job_type in UPSCALE_JOBS or job.job_type == "transcode":
        asset_id = payload.get("master_asset_id") or (scene.assets and scene.assets.master)
        kind, suffix = "master", "mp4"
    elif job.job_type in VIDEO_JOBS:
        asset_id = payload.get("keyframe_asset_id") or (scene.assets and scene.assets.keyframe)
        kind, suffix = "keyframe", "png"

    if asset_id:
        _attach_input(db, claim, asset_id, kind, suffix)
    elif job.job_type in UPSCALE_JOBS | {"align", "transcode"}:
        raise NotFoundError(f"Missing {kind} input for {job.job_type}")

    if job.job_type in VIDEO_JOBS | UPSCALE_JOBS | {"keyframe_qwen"}:
        payload.update(aspect=doc.format.aspect, duration_ms=scene.end_ms - scene.start_ms)
        claim.comfy_graph = build_comfy_graph(
            job_type=job.job_type, scene=scene, aspect=doc.format.aspect,
            target_res="4k" if job.job_type in UPSCALE_JOBS else doc.format.target,
            style_look=doc.style_bible.look, negative_prompt=doc.style_bible.negative,
            input_filename=claim.input_filename, gpu_model=worker.gpu_model, vram_gb=worker.vram_gb,
        )
    claim.payload = payload
    return claim

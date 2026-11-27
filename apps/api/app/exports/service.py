"""Prepare inputs for a frozen export and publish its two verified outputs."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import sign_media_token
from app.core.errors import NotFoundError, ValidationFailedError
from app.events.service import emit_event
from app.jobs.service import compute_idempotency_key, enqueue_job
from app.models import Asset, Export, Job, TimelineRevision
from app.timeline.schemas import TimelineDocument, VoiceClipItem


def _enqueue_dependency(
    db: Session, project_id: str, scene_id: str, job_type: str, payload: dict,
    *, depends_on: list[str] | None = None, est_cu: float = 0.0,
) -> Job:
    # Two exports may request identical inputs at once. Keep the winner's job,
    # or create a new attempt chain if that input previously exhausted its retries.
    salt = None
    while True:
        try:
            with db.begin_nested():
                return enqueue_job(
                    db, project_id=project_id, scene_id=scene_id, job_type=job_type, capability=job_type,
                    payload=payload, depends_on=depends_on, est_cu=est_cu, salt=salt,
                )
        except IntegrityError:
            key_payload = {**payload, "_salt": salt} if salt else payload
            key = compute_idempotency_key(project_id, scene_id, job_type, key_payload)
            existing = db.query(Job).filter_by(idempotency_key=key).one_or_none()
            if existing is None:
                raise
            if existing.status != "dead":
                return existing
            salt = existing.id


def enqueue_export_dependencies(db: Session, doc: TimelineDocument, resolution: str) -> list[str]:
    deps = []
    voice_by_scene = {clip.scene_id: clip for clip in doc.tracks.voice}
    captions = doc.tracks.captions
    for scene in doc.tracks.visual:
        refs = scene.assets
        has_clip = refs and (refs.master or (resolution == "4k" and refs.upscaled_4k))
        if scene.kind == "ai_video" and not has_clip:
            raise ValidationFailedError(f"Scene {scene.id} needs a full-resolution clip before export")
        if scene.kind == "image_motion" and not (has_clip or (refs and refs.keyframe)):
            raise ValidationFailedError(f"Scene {scene.id} needs a keyframe or a full-resolution clip before export")
        if resolution == "4k" and scene.kind != "diagram" and refs and refs.master and not refs.upscaled_4k:
            up = _enqueue_dependency(
                db, doc.project_id, scene.id, "upscale_seedvr2",
                {"master_asset_id": refs.master, "revision": doc.revision}, est_cu=0.12,
            )
            deps.append(up.id)
        if not scene.narration.strip():
            continue
        clip = voice_by_scene.get(scene.id)
        # Speech inputs are fully captured here, so unrelated timeline edits can reuse them.
        speech = {"narration": scene.narration, "voice": doc.voice.model_dump()}
        tts_deps = []
        if clip and clip.asset:
            source = {"audio_asset_id": clip.asset}
        else:
            tts = _enqueue_dependency(db, doc.project_id, scene.id, "tts", speech)
            deps.append(tts.id)
            tts_deps.append(tts.id)
            source = {"tts_job_id": tts.id}
        if not captions.words and not captions.words_asset:
            align = _enqueue_dependency(db, doc.project_id, scene.id, "align", {**speech, **source}, depends_on=tts_deps)
            deps.append(align.id)
    return deps


def _job_export(db: Session, job: Job) -> Export:
    exp = db.query(Export).filter_by(id=job.payload["export_id"], project_id=job.project_id).one_or_none()
    if exp is None:
        raise NotFoundError("Export for render job is missing")
    return exp


def build_render_payload(db: Session, job: Job) -> dict:
    exp = _job_export(db, job)
    revision = db.query(TimelineRevision).filter_by(project_id=exp.project_id, revision=exp.revision).one_or_none()
    if revision is None:
        raise NotFoundError(f"Timeline revision {exp.revision} for export {exp.id} is missing")
    doc = TimelineDocument.model_validate(revision.document)
    scenes = {scene.id: scene for scene in doc.tracks.visual}
    voices = {clip.scene_id: clip for clip in doc.tracks.voice}
    scene_words: dict[str, str] = {}

    # Only consume artifacts from the dependencies of this specific render. The live
    # timeline may already refer to a newly generated clip or a different voice.
    deps = db.query(Job).filter(Job.project_id == exp.project_id, Job.id.in_(job.depends_on or [])).all()
    for dep in deps:
        scene = scenes.get(dep.scene_id)
        if scene is None:
            continue
        outputs = (dep.result or {}).get("asset_ids", {})
        if dep.job_type == "tts":
            clip = voices.get(scene.id)
            if clip is None:
                clip = VoiceClipItem(scene_id=scene.id, start_ms=scene.start_ms, end_ms=scene.end_ms)
                doc.tracks.voice.append(clip)
                voices[scene.id] = clip
            clip.asset = outputs["audio"]
        elif dep.job_type == "align":
            scene_words[scene.id] = outputs["words"]
        elif dep.job_type in {"upscale_seedvr2", "upscale_flashvsr"} and scene.assets:
            scene.assets.upscaled_4k = outputs["upscaled_4k"]

    # EduComposition resolves proxy before master for editor previews. Use the
    # export rendition in its master slot, and never give the renderer a proxy URL.
    kinds = {aid: {"words"} for aid in scene_words.values()}
    for scene in doc.tracks.visual:
        if not scene.assets or scene.kind == "diagram":
            continue
        refs = scene.assets
        refs.proxy = None
        if exp.resolution == "4k" and refs.upscaled_4k:
            refs.master = refs.upscaled_4k
            kinds[refs.master] = {"upscaled_4k"}
        elif refs.master:
            kinds[refs.master] = {"master", "upscaled_4k"}
        elif refs.keyframe:
            kinds[refs.keyframe] = {"keyframe"}
    kinds.update({clip.asset: {"audio"} for clip in doc.tracks.voice if clip.asset})
    if doc.tracks.music.asset:
        kinds[doc.tracks.music.asset] = {"music", "audio"}
    if doc.tracks.captions.words_asset:
        kinds[doc.tracks.captions.words_asset] = {"words"}
    rows = db.query(Asset.id, Asset.kind).filter(
        Asset.project_id == exp.project_id, Asset.sync_state == "synced", Asset.id.in_(kinds),
    ).all()
    found = {aid for aid, kind in rows if kind in kinds[aid]}
    if found != kinds.keys():
        raise NotFoundError(f"Missing synced render inputs for export {exp.id}")
    base = get_settings().public_api_url.rstrip("/")
    urls = {aid: f"{base}/v1/media/{aid}?t={sign_media_token(aid)}" for aid in found}
    return {
        **job.payload,
        "resolution": exp.resolution,
        "revision": exp.revision,
        "document": doc.model_dump(mode="json"),
        "media_urls": urls,
        "scene_words": scene_words,
    }


def finish_export(db: Session, job: Job, created_assets: dict[str, Asset]) -> None:
    """Record the ready event in the same transaction as worker completion."""
    exp = _job_export(db, job)
    if set(created_assets) != {"export", "srt"}:
        raise ValidationFailedError("A render must upload both its MP4 and captions.srt")
    master = created_assets["export"]
    captions = created_assets["srt"]
    exp.master_asset_id = master.id
    exp.srt_asset_id = captions.id
    exp.status = "ready"
    emit_event(db, "export.ready", {"export_id": exp.id, "resolution": exp.resolution}, project_id=exp.project_id)

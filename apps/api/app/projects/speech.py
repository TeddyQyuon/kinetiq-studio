"""Immutable speech inputs shared by generation, re-voice, and frozen exports."""

from __future__ import annotations

import json

from pydantic import TypeAdapter
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailedError
from app.jobs.service import compute_idempotency_key, enqueue_job
from app.models import Asset, Job
from app.storage.drive import StorageProvider, VerifiedFile
from app.timeline.schemas import TimelineDocument, VisualSceneItem, VoiceClipItem, WordTiming

MAX_WORD_BYTES = 1024 * 1024
word_list = TypeAdapter(list[WordTiming])


def enqueue_dependency(
    db: Session,
    project_id: str,
    scene_id: str,
    job_type: str,
    payload: dict,
    *,
    depends_on: list[str] | None = None,
    est_cu: float = 0.0,
    salt: str | None = None,
) -> Job:
    # The same input can be requested concurrently, or after exhausting retries.
    connection = db.connection()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        # sqlite3 legacy mode otherwise commits the first released SAVEPOINT
        # independently of the revision/command transaction that requested it.
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    while True:
        try:
            with db.begin_nested():
                return enqueue_job(
                    db,
                    project_id=project_id,
                    scene_id=scene_id,
                    job_type=job_type,
                    capability=job_type,
                    payload=payload,
                    depends_on=depends_on,
                    est_cu=est_cu,
                    salt=salt,
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


def speech_inputs(doc: TimelineDocument, scene: VisualSceneItem) -> dict:
    return {"narration": scene.narration, "voice": doc.voice.model_dump()}


def latest_tts(db: Session, project_id: str, scene_id: str, speech: dict) -> Job | None:
    candidates = (
        db.query(Job)
        .filter_by(project_id=project_id, scene_id=scene_id, job_type="tts")
        .filter(
            Job.payload["narration"].as_string() == speech["narration"],
        )
        .order_by(Job.created_at.desc(), Job.id.desc())
    )
    return next((j for j in candidates if j.payload == speech), None)


def output_asset(db: Session, job: Job, kind: str) -> Asset | None:
    asset_id = (job.result or {}).get("asset_ids", {}).get(kind)
    if asset_id:
        return db.query(Asset).filter_by(id=asset_id, project_id=job.project_id, kind=kind, sync_state="synced").first()
    return None


def synced_voice(db: Session, doc: TimelineDocument, scene: VisualSceneItem) -> tuple[str | None, Job | None]:
    clip = next((v for v in doc.tracks.voice if v.scene_id == scene.id and v.asset), None)
    if (
        not clip
        or not db.query(Asset.id)
        .filter_by(
            project_id=doc.project_id,
            id=clip.asset,
            kind="audio",
            sync_state="synced",
        )
        .first()
    ):
        return None, None
    producer = (
        db.query(Job)
        .filter_by(project_id=doc.project_id, job_type="tts", status="succeeded")
        .filter(
            Job.result["asset_ids"]["audio"].as_string() == clip.asset,
        )
        .first()
    )
    if producer and "narration" in producer.payload and producer.payload != speech_inputs(doc, scene):
        return None, None
    # A split or imported clip can reference audio made for a different scene.
    # Align that asset directly, since parent-job claims are scoped to their scene.
    return clip.asset, producer if producer and producer.scene_id == scene.id else None


def alignment_job(
    db: Session, doc: TimelineDocument, scene: VisualSceneItem, *, tts: Job | None, audio: str | None = None
) -> Job:
    source = {"tts_job_id": tts.id} if tts else {"audio_asset_id": audio}
    payload = {**speech_inputs(doc, scene), **source}
    salt = None
    while True:
        job = enqueue_dependency(
            db,
            doc.project_id,
            scene.id,
            "align",
            payload,
            salt=salt,
            depends_on=[tts.id] if tts else [],
        )
        if job.status != "succeeded" or ("word_timings" in (job.result or {}) and output_asset(db, job, "words")):
            return job
        # Legacy workers recorded only an asset ID. Rehydrate via a verified
        # alignment before merging its timings into a partially populated track.
        salt = job.id


def speech_jobs(
    db: Session, doc: TimelineDocument, scene: VisualSceneItem, *, revoice: bool = False
) -> tuple[Job, Job]:
    speech = speech_inputs(doc, scene)
    current = latest_tts(db, doc.project_id, scene.id, speech)
    if current is None:
        current = enqueue_dependency(db, doc.project_id, scene.id, "tts", speech)
    elif current.status == "dead" or (
        current.status == "succeeded" and (revoice or not output_asset(db, current, "audio"))
    ):
        current = enqueue_dependency(db, doc.project_id, scene.id, "tts", speech, salt=current.id)
    return current, alignment_job(db, doc, scene, tts=current)


def clear_scene_words(doc: TimelineDocument, scene: VisualSceneItem) -> None:
    captions = doc.tracks.captions
    captions.words_asset = None
    if captions.words:
        captions.words = [w for w in captions.words if not scene.start_ms <= w.start_ms < scene.end_ms]


def clear_scene_voice(doc: TimelineDocument, scene: VisualSceneItem) -> None:
    for clip in doc.tracks.voice:
        if clip.scene_id == scene.id:
            clip.asset = None
    clear_scene_words(doc, scene)


def visual_status(scene: VisualSceneItem) -> str:
    return (
        "ready"
        if scene.kind == "diagram" or (scene.assets and (scene.assets.master or scene.assets.keyframe))
        else "planned"
    )


def retime_speech(doc: TimelineDocument, previous: list[VisualSceneItem]) -> None:
    """Move existing takes and their absolute word timings with their scene."""
    starts = {item.id: item.start_ms for item in doc.tracks.visual}
    shifts = {item.id: starts[item.id] - item.start_ms for item in previous if item.id in starts}
    doc.tracks.voice = [clip for clip in doc.tracks.voice if clip.scene_id in starts]
    for clip in doc.tracks.voice:
        shift = shifts.get(clip.scene_id, 0)
        clip.start_ms += shift
        clip.end_ms += shift

    # Only changed/deleted spans can affect an already-published word. Use the
    # old spans for attribution, including when scenes swap places.
    changed = [item for item in previous if item.id not in starts or shifts[item.id] != 0]
    if not changed:
        return
    captions = doc.tracks.captions
    captions.words_asset = None
    if captions.words:
        updated = []
        for word in captions.words:
            owner = next((item for item in changed if item.start_ms <= word.start_ms < item.end_ms), None)
            if owner:
                if owner.id not in starts:
                    continue
                word.start_ms += shifts[owner.id]
                word.end_ms += shifts[owner.id]
            updated.append(word)
        captions.words = sorted(updated, key=lambda w: (w.start_ms, w.end_ms))


def use_scene_audio(doc: TimelineDocument, scene: VisualSceneItem, audio: Asset) -> None:
    old_end = scene.end_ms
    duration = audio.duration_ms or 0
    if duration > 0 and abs(duration - (old_end - scene.start_ms)) > 250:
        scene.end_ms = scene.start_ms + max(1500, duration)
        following = doc.tracks.visual[doc.tracks.visual.index(scene) + 1:]
        previous = [item.model_copy() for item in following]
        cursor = scene.end_ms
        for item in following:
            length = item.end_ms - item.start_ms
            gap = max(0, item.start_ms - old_end)
            old_end = item.end_ms
            item.start_ms = cursor + gap
            item.end_ms = item.start_ms + length
            cursor = item.end_ms
        retime_speech(doc, previous)
    clip = next((v for v in doc.tracks.voice if v.scene_id == scene.id), None)
    if clip is None:
        clip = VoiceClipItem(scene_id=scene.id, start_ms=scene.start_ms, end_ms=scene.end_ms)
        doc.tracks.voice.append(clip)
    clip.asset = audio.id
    clip.start_ms = scene.start_ms
    clip.end_ms = scene.start_ms + duration if duration > 0 else scene.end_ms
    if scene.status == "generating_audio":
        scene.status = visual_status(scene)


def merge_scene_words(doc: TimelineDocument, scene: VisualSceneItem, words: list[WordTiming]) -> None:
    clear_scene_words(doc, scene)
    merged = doc.tracks.captions.words or []
    for word in words:
        start = scene.start_ms + word.start_ms
        if start < scene.end_ms:
            merged.append(
                WordTiming(word=word.word, start_ms=start, end_ms=min(scene.end_ms, scene.start_ms + word.end_ms))
            )
    doc.tracks.captions.words = sorted(merged, key=lambda w: (w.start_ms, w.end_ms))


async def read_word_timings(provider: StorageProvider, *, owner_id: str, verified: VerifiedFile) -> list[WordTiming]:
    if verified.size_bytes > MAX_WORD_BYTES:
        raise ValidationFailedError("Word timing JSON exceeds 1 MiB")
    data, total, _ = await provider.read_range(
        user_id=owner_id,
        storage_key=verified.storage_key,
        drive_file_id=verified.drive_file_id,
        start=0,
        end=MAX_WORD_BYTES - 1,
    )
    if total != verified.size_bytes or len(data) != verified.size_bytes:
        raise ValidationFailedError("Word timing JSON has an invalid size")
    try:
        raw = json.loads(data)
        words = word_list.validate_python(raw.get("words") if isinstance(raw, dict) else raw)
    except (ValueError, RecursionError) as exc:
        raise ValidationFailedError("Invalid word timing JSON") from exc
    if any(not w.word.strip() or not 0 <= w.start_ms <= w.end_ms for w in words):
        raise ValidationFailedError("Invalid word or time range in alignment")
    return words


def prepare_scene_speech(
    db: Session, doc: TimelineDocument, scene: VisualSceneItem, *, revoice: bool = False
) -> tuple[Job, Job]:
    tts, align = speech_jobs(db, doc, scene, revoice=revoice)
    clear_scene_voice(doc, scene)
    if scene.status in {"ready", "planned", "generating_audio"}:
        scene.status = "generating_audio"
    # Undo, revision restore or switching voices can select completed immutable inputs.
    if tts.status == "succeeded" and (audio := output_asset(db, tts, "audio")):
        use_scene_audio(doc, scene, audio)
        if align.status == "succeeded":
            merge_scene_words(doc, scene, word_list.validate_python(align.result["word_timings"]))
    return tts, align


def apply_speech_completion(
    db: Session,
    doc: TimelineDocument,
    scene: VisualSceneItem,
    job: Job,
    assets: dict[str, Asset],
    words: list[WordTiming] | None,
) -> bool:
    """Publish only the current take. Old revisions still own their immutable job outputs."""
    speech = speech_inputs(doc, scene)
    if any(job.payload.get(key) != value for key, value in speech.items()):
        return False
    if job.job_type == "tts":
        newest = latest_tts(db, doc.project_id, scene.id, speech)
        if newest is None or newest.id != job.id:
            return False
        clear_scene_words(doc, scene)
        use_scene_audio(doc, scene, assets["audio"])
        return True

    audio = job.payload.get("audio_asset_id")
    if parent_id := job.payload.get("tts_job_id"):
        parent = db.get(Job, parent_id)
        audio = (parent.result or {}).get("asset_ids", {}).get("audio") if parent else None
    if words is None or not audio or not any(v.scene_id == scene.id and v.asset == audio for v in doc.tracks.voice):
        return False
    merge_scene_words(doc, scene, words)
    return True

from __future__ import annotations

import uuid

from sqlalchemy import and_, case, func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import sign_media_token
from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.director.service import build_deterministic_storyboard, storyboard_to_timeline
from app.events.service import emit_event
from app.identity.service import ensure_default_owner
from app.jobs.service import enqueue_job
from app.models import Asset, Job, Project, QAReview, Scene, TimelineRevision
from app.projects.media import MusicInput, store_music
from app.projects.speech import enqueue_dependency, prepare_scene_speech, synced_voice
from app.qa.service import CLIP_MODELS
from app.projects.schemas import MusicUploadResponse, ProjectCreateRequest, ProjectDetailRead, ProjectSummaryRead
from app.timeline.schemas import MusicTrackConfig, SceneQASummary, TimelineCommand, TimelineDocument
from app.timeline.service import apply_timeline_commands, get_latest_timeline, save_timeline_document, sync_scenes_projection


def build_project_summary(db: Session, project: Project) -> ProjectSummaryRead:
    scenes = db.query(Scene).filter(Scene.project_id == project.id).all()
    return ProjectSummaryRead(
        id=project.id,
        title=project.title,
        topic=project.topic,
        aspect=project.aspect,
        target_res=project.target_res,
        duration_preset=project.duration_preset,
        qa_policy=project.qa_policy,
        brain_mode=project.brain_mode,
        status=project.status,
        drive_folder_id=project.drive_folder_id,
        current_revision=project.current_revision,
        scene_count=len(scenes),
        ready_scenes=sum(1 for s in scenes if s.status == "ready"),
        needs_review_count=sum(1 for s in scenes if s.status == "needs_review"),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def build_project_detail(db: Session, project: Project) -> ProjectDetailRead:
    settings = get_settings()
    summary = build_project_summary(db, project)
    doc = get_latest_timeline(db, project.id)
    assets = db.query(Asset).filter(Asset.project_id == project.id).all()
    media_urls: dict[str, str] = {}
    base = settings.public_api_url.rstrip("/")
    for a in assets:
        tok = sign_media_token(a.id, ttl_seconds=600)
        media_urls[a.id] = f"{base}/v1/media/{a.id}?t={tok}"
    return ProjectDetailRead(**summary.model_dump(), timeline=doc, media_urls=media_urls)


def create_project_with_initial_timeline(db: Session, body: ProjectCreateRequest) -> Project:
    owner = ensure_default_owner(db)
    project = Project(
        owner_id=owner.id,
        title=body.title,
        topic=body.topic,
        aspect=body.aspect,
        target_res=body.target_res,
        duration_preset=body.duration_preset,
        qa_policy=body.qa_policy,
        brain_mode=body.brain_mode,
        status="planned",
        current_revision=1,
    )
    db.add(project)
    db.flush()

    plan = build_deterministic_storyboard(
        topic=body.topic,
        aspect=body.aspect,
        duration_preset=body.duration_preset,
        script_override=body.script_override,
    )
    doc = storyboard_to_timeline(project, plan, revision=1)
    db.add(
        TimelineRevision(
            project_id=project.id,
            revision=1,
            document=doc.model_dump(mode="json"),
            author="ai",
            command_batch=[],
        )
    )
    sync_scenes_projection(db, project.id, doc)
    emit_event(db, "project.created", {"project_id": project.id, "title": project.title}, project_id=project.id)
    db.commit()
    db.refresh(project)
    return project


def dispatch_generation_pipeline(db: Session, project: Project) -> list[str]:
    doc = get_latest_timeline(db, project.id)
    queued_job_ids: list[str] = []

    for scene in doc.tracks.visual:
        if scene.kind != "diagram" and not (scene.assets and (scene.assets.master or scene.assets.keyframe)):
            job = enqueue_dependency(
                db, project.id, scene.id, "keyframe_qwen",
                {"seed": scene.seed if scene.seed is not None else 42, "aspect": doc.format.aspect},
                est_cu=0.02,
            )
            queued_job_ids.append(job.id)
            scene.status = "generating_keyframe"
        if scene.narration.strip() and not synced_voice(db, doc, scene)[0]:
            tts, align = prepare_scene_speech(db, doc, scene)
            queued_job_ids.extend([tts.id, align.id])

    project.status = "generating"
    emit_event(
        db,
        "pipeline.started",
        {"project_id": project.id, "queued_jobs": len(queued_job_ids)},
        project_id=project.id,
    )
    save_timeline_document(db, project, doc.revision, doc, author="ai")
    return queued_job_ids


def enqueue_scene_revoice(db: Session, project: Project, scene_id: str) -> dict:
    doc = get_latest_timeline(db, project.id)
    scene = next((s for s in doc.tracks.visual if s.id == scene_id), None)
    if scene is None:
        raise NotFoundError(f"Scene {scene_id} not found")
    if not scene.narration.strip():
        raise ValidationFailedError("A scene needs narration before re-voicing")
    tts, align = prepare_scene_speech(db, doc, scene, revoice=True)
    save_timeline_document(db, project, doc.revision, doc, author="user")
    return {"ok": True, "tts_job_id": tts.id, "align_job_id": align.id}


def approve_scene_and_dispatch(db: Session, project: Project, scene_id: str) -> dict:
    from pydantic import TypeAdapter
    cmd = TypeAdapter(TimelineCommand).validate_python({"type": "approve_scene_qa", "scene_id": scene_id})
    doc = apply_timeline_commands(db, project.id, project.current_revision, [cmd], author="user")
    scene = next((s for s in doc.tracks.visual if s.id == scene_id), None)

    queued_job_id: str | None = None
    if scene and scene.kind == "ai_video" and not (scene.assets and scene.assets.master):
        cap = "i2v_wan22" if scene.model == "wan22_i2v" else "i2v_ltx25"
        job = enqueue_job(
            db,
            project_id=project.id,
            scene_id=scene.id,
            job_type=cap,
            capability=cap,
            payload={
                "keyframe_asset_id": scene.assets.keyframe if scene.assets else None,
                "seed": scene.seed,
            },
            est_cu=0.06,
            salt=uuid.uuid4().hex[:6],
        )
        queued_job_id = job.id
        db.commit()
    return {"ok": True, "scene_id": scene_id, "queued_job_id": queued_job_id}

def project_qa_analytics(db: Session, project_id: str) -> dict:
    # A review describes a particular render. Editing a scene's model later must
    # never reattribute old results. Legacy keyframes were all rendered by Qwen.
    model = case(
        *[(Job.job_type == job_type, name) for job_type, name in CLIP_MODELS.items()],
        (Asset.kind == "keyframe", "qwen_image"),
    )
    rows = (
        db.query(
            model,
            func.count(QAReview.id),
            func.sum(case((QAReview.verdict == "passed", 1), else_=0)),
            func.sum(case((QAReview.fix_action.in_(["rerender_clip", "rewrite_prompt_and_reseed"]), 1), else_=0)),
        )
        .select_from(QAReview)
        .outerjoin(Job, and_(Job.id == QAReview.id, Job.project_id == QAReview.project_id))
        .outerjoin(Asset, and_(Asset.id == QAReview.asset_id, Asset.project_id == QAReview.project_id))
        .filter(QAReview.project_id == project_id, QAReview.verdict != "superseded")
        .group_by(model)
        .all()
    )
    models = {
        name: {"reviews": 0, "passed": 0, "pass_rate": None, "retry_count": 0}
        for name in (*CLIP_MODELS.values(), "qwen_image")
    }
    for name, total, passed, retries in rows:
        if name in models:
            models[name] = {"reviews": total, "passed": passed, "pass_rate": passed / total, "retry_count": retries}
    return {"project_id": project_id, "models": models}


def check_project_revision(project: Project, expected: int | None) -> int:
    if expected is not None and expected != project.current_revision:
        raise ConflictError(
            f"Revision conflict: expected {expected}, server is at {project.current_revision}",
            details={"server_revision": project.current_revision},
        )
    return project.current_revision


async def attach_music(db: Session, project: Project, music: MusicInput, revision: int) -> MusicUploadResponse:
    asset = await store_music(db, project, music, revision)
    db.add(asset)
    emit_event(db, "asset.synced", {"asset_id": asset.id, "kind": "music"}, project_id=project.id)
    # Keep the verified asset discoverable even if a concurrent edit wins the timeline revision.
    db.commit()
    db.refresh(project)
    check_project_revision(project, revision)
    doc = get_latest_timeline(db, project.id)
    doc.tracks.music = MusicTrackConfig(asset=asset.id, **music.options.model_dump())
    saved = save_timeline_document(db, project, revision, doc)
    base = get_settings().public_api_url.rstrip("/")
    return MusicUploadResponse(
        asset_id=asset.id, media_url=f"{base}/v1/media/{asset.id}?t={sign_media_token(asset.id)}", timeline=saved,
    )


def detach_music(db: Session, project: Project, revision: int) -> TimelineDocument:
    doc = get_latest_timeline(db, project.id)
    doc.tracks.music.asset = None
    return save_timeline_document(db, project, revision, doc)


def duplicate_project(db: Session, source: Project, title: str | None) -> Project:
    doc = get_latest_timeline(db, source.id)
    duplicate = Project(
        owner_id=source.owner_id, title=title or f"{source.title[:248]} (Copy)", topic=source.topic,
        aspect=source.aspect, target_res=source.target_res, duration_preset=source.duration_preset,
        qa_policy=source.qa_policy, brain_mode=source.brain_mode, status="planned", current_revision=0,
    )
    db.add(duplicate)
    db.flush()
    doc.project_id = duplicate.id
    doc.tracks.voice = []
    doc.tracks.captions.words = None
    doc.tracks.captions.words_asset = None
    doc.tracks.music.asset = None
    for i, scene in enumerate(doc.tracks.visual, 1):
        scene.id = f"{duplicate.id}_scn_{i:03d}"
        scene.assets = None
        scene.source_start_ms = 0
        if scene.kind == "diagram":
            scene.status = "ready"
        else:
            scene.status = "planned"
            scene.qa = SceneQASummary(score=None, attempts=0, verdict="pending")
    emit_event(db, "project.created", {"project_id": duplicate.id, "title": duplicate.title}, project_id=duplicate.id)
    save_timeline_document(db, duplicate, 0, doc)
    return duplicate

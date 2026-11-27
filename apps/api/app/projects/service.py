from __future__ import annotations

import uuid

from sqlalchemy import and_, case, func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import sign_media_token
from app.director.service import build_deterministic_storyboard, storyboard_to_timeline
from app.events.service import emit_event
from app.identity.service import ensure_default_owner
from app.jobs.service import enqueue_job
from app.models import Asset, Job, Project, QAReview, Scene, TimelineRevision
from app.qa.service import CLIP_MODELS
from app.projects.schemas import ProjectCreateRequest, ProjectDetailRead, ProjectSummaryRead
from app.timeline.schemas import TimelineCommand
from app.timeline.service import apply_timeline_commands, get_latest_timeline, sync_scenes_projection


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
        if scene.kind == "diagram":
            continue
        if scene.status == "ready" and scene.assets and scene.assets.master:
            continue
        job = enqueue_job(
            db,
            project_id=project.id,
            scene_id=scene.id,
            job_type="keyframe_qwen",
            capability="keyframe_qwen",
            payload={"seed": scene.seed or 42, "aspect": doc.format.aspect},
            est_cu=0.02,
        )
        queued_job_ids.append(job.id)
        scene.status = "generating_keyframe"

    project.status = "generating"
    emit_event(
        db,
        "pipeline.started",
        {"project_id": project.id, "queued_jobs": len(queued_job_ids)},
        project_id=project.id,
    )
    db.commit()
    return queued_job_ids


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

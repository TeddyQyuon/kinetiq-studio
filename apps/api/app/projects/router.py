from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import NotFoundError
from app.director.schemas import (
    ChatGPTImportRequest,
    CopilotAcceptRequest,
    CopilotChatRequest,
    CopilotProposalResponse,
    DirectorBriefResponse,
    PlanRequest,
)
from app.director.service import (
    accept_copilot_proposal,
    build_chatgpt_brief,
    create_copilot_proposal,
    import_chatgpt_storyboard,
    plan_project_storyboard,
    reject_copilot_proposal,
)
from app.jobs.schemas import JobRead
from app.jobs.service import enqueue_job
from app.models import Job, Project, QAReview
from app.projects.schemas import (
    ProjectCreateRequest,
    ProjectDetailRead,
    ProjectSummaryRead,
    ProjectUpdateRequest,
    SceneRegenerateRequest,
)
from app.projects.service import (
    approve_scene_and_dispatch,
    build_project_detail,
    build_project_summary,
    create_project_with_initial_timeline,
    dispatch_generation_pipeline,
    project_qa_analytics,
)
from app.qa.consumer import clip_qa_lifespan
from app.qa.schemas import QAReviewRead
from app.timeline.schemas import TimelineCommand, TimelineDocument
from app.timeline.service import apply_timeline_commands

router = APIRouter(prefix="/v1/projects", tags=["projects"], lifespan=clip_qa_lifespan)


def _get_project(db: Session, project_id: str) -> Project:
    proj = db.query(Project).filter(Project.id == project_id).first()
    if not proj:
        raise NotFoundError(f"Project {project_id} not found")
    return proj


@router.get("", response_model=list[ProjectSummaryRead])
def list_projects(db: Session = Depends(get_db)) -> list[ProjectSummaryRead]:
    rows = db.query(Project).order_by(Project.updated_at.desc()).all()
    return [build_project_summary(db, p) for p in rows]


@router.post("", response_model=ProjectDetailRead)
def create_project(body: ProjectCreateRequest, db: Session = Depends(get_db)) -> ProjectDetailRead:
    proj = create_project_with_initial_timeline(db, body)
    return build_project_detail(db, proj)


@router.get("/{project_id}", response_model=ProjectDetailRead)
def get_project(project_id: str, db: Session = Depends(get_db)) -> ProjectDetailRead:
    return build_project_detail(db, _get_project(db, project_id))


@router.patch("/{project_id}", response_model=ProjectSummaryRead)
def update_project(project_id: str, body: ProjectUpdateRequest, db: Session = Depends(get_db)) -> ProjectSummaryRead:
    proj = _get_project(db, project_id)
    if body.title is not None:
        proj.title = body.title
    if body.qa_policy is not None:
        proj.qa_policy = body.qa_policy
    if body.target_res is not None:
        proj.target_res = body.target_res
    db.commit()
    db.refresh(proj)
    return build_project_summary(db, proj)


@router.delete("/{project_id}")
def delete_project(project_id: str, db: Session = Depends(get_db)) -> dict[str, bool]:
    proj = _get_project(db, project_id)
    db.delete(proj)
    db.commit()
    return {"ok": True}


@router.post("/{project_id}/plan", response_model=TimelineDocument)
async def plan_project(
    project_id: str,
    body: PlanRequest,
    db: Session = Depends(get_db),
) -> TimelineDocument:
    proj = _get_project(db, project_id)
    return await plan_project_storyboard(
        db,
        proj,
        script_override=body.script_override,
        brain_mode=body.brain_mode,
    )


@router.get("/{project_id}/plan/brief", response_model=DirectorBriefResponse)
def get_chatgpt_brief(project_id: str, db: Session = Depends(get_db)) -> DirectorBriefResponse:
    return build_chatgpt_brief(_get_project(db, project_id))


@router.post("/{project_id}/plan/import", response_model=TimelineDocument)
def import_chatgpt_plan(
    project_id: str,
    body: ChatGPTImportRequest,
    db: Session = Depends(get_db),
) -> TimelineDocument:
    proj = _get_project(db, project_id)
    return import_chatgpt_storyboard(db, proj, body.raw_json)


@router.post("/{project_id}/generate")
def generate_project(project_id: str, db: Session = Depends(get_db)) -> dict:
    proj = _get_project(db, project_id)
    job_ids = dispatch_generation_pipeline(db, proj)
    return {"ok": True, "queued_jobs": len(job_ids), "job_ids": job_ids}


@router.get("/{project_id}/jobs", response_model=list[JobRead])
def list_project_jobs(project_id: str, db: Session = Depends(get_db)) -> list[JobRead]:
    rows = db.query(Job).filter(Job.project_id == project_id).order_by(Job.created_at.desc()).limit(100).all()
    return [
        JobRead(
            id=j.id,
            project_id=j.project_id,
            scene_id=j.scene_id,
            job_type=j.job_type,
            capability=j.capability,
            status=j.status,
            attempts=j.attempts,
            max_attempts=j.max_attempts,
            progress_pct=j.progress_pct,
            gpu_seconds=j.gpu_seconds,
            est_cu=j.est_cu,
            error_message=j.error_message,
            created_at=j.created_at,
        )
        for j in rows
    ]


@router.get("/{project_id}/qa-analytics")
def qa_analytics(project_id: str, db: Session = Depends(get_db)) -> dict:
    """Pass fraction per reviewed render and number of QA-requested rerenders."""
    return project_qa_analytics(db, _get_project(db, project_id).id)


@router.get("/{project_id}/qa-reviews", response_model=list[QAReviewRead])
def list_qa_reviews(project_id: str, db: Session = Depends(get_db)) -> list[QAReviewRead]:
    rows = (
        db.query(QAReview)
        .filter(QAReview.project_id == project_id)
        .order_by(QAReview.created_at.desc())
        .limit(100)
        .all()
    )
    return [
        QAReviewRead(
            id=r.id,
            scene_id=r.scene_id,
            asset_id=r.asset_id,
            narration_match=r.narration_match,
            artifact_free=r.artifact_free,
            style_consistency=r.style_consistency,
            safe_zone=r.safe_zone,
            score=r.score,
            verdict=r.verdict,
            reason=r.reason,
            fix_action=r.fix_action,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.post("/{project_id}/scenes/{scene_id}/approve")
def approve_scene(project_id: str, scene_id: str, db: Session = Depends(get_db)) -> dict:
    proj = _get_project(db, project_id)
    return approve_scene_and_dispatch(db, proj, scene_id)


@router.post("/{project_id}/scenes/{scene_id}/regenerate")
def regenerate_scene(
    project_id: str,
    scene_id: str,
    body: SceneRegenerateRequest,
    db: Session = Depends(get_db),
) -> dict:
    from pydantic import TypeAdapter
    proj = _get_project(db, project_id)
    cmd = TypeAdapter(TimelineCommand).validate_python(
        {
            "type": "regenerate_clip",
            "scene_id": scene_id,
            "prompt": body.prompt,
            "model": body.model,
            "camera": body.camera,
            "seed": body.seed,
        }
    )
    doc = apply_timeline_commands(db, proj.id, proj.current_revision, [cmd], author="user")
    scene = next((s for s in doc.tracks.visual if s.id == scene_id), None)
    job = enqueue_job(
        db,
        project_id=proj.id,
        scene_id=scene_id,
        job_type="keyframe_qwen",
        capability="keyframe_qwen",
        payload={"seed": scene.seed if scene else 42},
        est_cu=0.02,
        salt=uuid.uuid4().hex[:6],
    )
    db.commit()
    return {"ok": True, "revision": doc.revision, "queued_job_id": job.id}


@router.post("/{project_id}/copilot", response_model=CopilotProposalResponse)
async def copilot_chat(
    project_id: str,
    body: CopilotChatRequest,
    db: Session = Depends(get_db),
) -> CopilotProposalResponse:
    proj = _get_project(db, project_id)
    return await create_copilot_proposal(
        db,
        proj,
        message=body.message,
        selected_scene_id=body.selected_scene_id,
    )


@router.post("/{project_id}/copilot/{proposal_id}/accept", response_model=TimelineDocument)
def copilot_accept(
    project_id: str,
    proposal_id: str,
    body: CopilotAcceptRequest,
    db: Session = Depends(get_db),
) -> TimelineDocument:
    return accept_copilot_proposal(
        db,
        project_id=project_id,
        proposal_id=proposal_id,
        expected_revision=body.expected_revision,
        confirm_gpu_spend=body.confirm_gpu_spend,
    )


@router.post("/{project_id}/copilot/{proposal_id}/reject")
def copilot_reject(
    project_id: str,
    proposal_id: str,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    return reject_copilot_proposal(db, project_id=project_id, proposal_id=proposal_id)

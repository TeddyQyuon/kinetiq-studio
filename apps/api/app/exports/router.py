from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import sign_media_token
from app.core.db import get_db
from app.core.errors import NotFoundError, ValidationFailedError
from app.events.service import emit_event
from app.exports.schemas import ExportCreateRequest, ExportRead
from app.exports.service import enqueue_export_dependencies
from app.jobs.service import enqueue_job
from app.models import Asset, Export, Project
from app.storage.downloads import asset_response
from app.timeline.service import get_latest_timeline

router = APIRouter(prefix="/v1", tags=["exports"])


def _to_export_read(db: Session, exp: Export) -> ExportRead:
    settings = get_settings()
    base = settings.public_api_url.rstrip("/")
    dl_url = f"{base}/v1/exports/{exp.id}/download" if exp.master_asset_id else None
    srt_url = None
    drive_id = None
    if exp.master_asset_id:
        ast = db.query(Asset).filter(Asset.id == exp.master_asset_id).first()
        drive_id = ast.drive_file_id if ast else None
    if exp.srt_asset_id:
        tok = sign_media_token(exp.srt_asset_id, ttl_seconds=600)
        srt_url = f"{base}/v1/media/{exp.srt_asset_id}?t={tok}"
    return ExportRead(
        id=exp.id,
        project_id=exp.project_id,
        revision=exp.revision,
        resolution=exp.resolution,
        status=exp.status,
        download_url=dl_url,
        srt_url=srt_url,
        drive_file_id=drive_id,
        publish_kit=exp.publish_kit or {},
        created_at=exp.created_at,
    )


@router.get("/projects/{project_id}/exports", response_model=list[ExportRead])
def list_exports(project_id: str, db: Session = Depends(get_db)) -> list[ExportRead]:
    rows = db.query(Export).filter(Export.project_id == project_id).order_by(Export.created_at.desc()).all()
    return [_to_export_read(db, e) for e in rows]


def queue_export(db: Session, proj: Project, resolution: str) -> Export:
    """Queue an export and its DAG within the caller's transaction."""
    doc = get_latest_timeline(db, proj.id)

    if doc.project_id != proj.id:
        raise ValidationFailedError("Project and timeline identifiers differ")

    if doc.revision != proj.current_revision:
        raise ValidationFailedError("Project and timeline revisions differ")

    first_narration = doc.tracks.visual[0].narration if doc.tracks.visual else proj.topic
    publish_kit = {
        "hook": first_narration,
        "seo_title": f"{proj.title} ({resolution.upper()})",
        "description": f"{proj.topic} — clear visual breakdown with animated charts & English narration.",
        "hashtags": ["#education", "#explainer", "#knowledge", "#learnontiktok"],
        "ai_disclosure_note": "Enable TikTok 'AI-generated content' & Facebook AI info label before posting to protect monetization.",
    }

    exp = Export(
        project_id=proj.id,
        revision=doc.revision,
        resolution=resolution,
        status="queued",
        publish_kit=publish_kit,
    )
    db.add(exp)
    db.flush()

    dep_ids = enqueue_export_dependencies(db, doc, resolution)
    enqueue_job(
        db,
        project_id=proj.id,
        scene_id=None,
        job_type="render",
        capability="render",
        payload={"export_id": exp.id, "revision": doc.revision, "resolution": resolution},
        depends_on=dep_ids,
    )
    emit_event(
        db,
        "export.queued",
        {"export_id": exp.id, "resolution": resolution},
        project_id=proj.id,
    )
    return exp


@router.post("/projects/{project_id}/exports", response_model=ExportRead)
def create_export(
    project_id: str,
    body: ExportCreateRequest,
    db: Session = Depends(get_db),
) -> ExportRead:
    proj = db.query(Project).filter(Project.id == project_id).first()
    if not proj:
        raise NotFoundError(f"Project {project_id} not found")
    exp = queue_export(db, proj, body.resolution)
    db.commit()
    db.refresh(exp)
    return _to_export_read(db, exp)


@router.get("/exports/{export_id}", response_model=ExportRead)
def get_export(export_id: str, db: Session = Depends(get_db)) -> ExportRead:
    exp = db.query(Export).filter(Export.id == export_id).first()
    if not exp:
        raise NotFoundError(f"Export {export_id} not found")
    return _to_export_read(db, exp)


@router.get("/exports/{export_id}/download")
async def download_export(
    export_id: str, request: Request, inline: bool = False, db: Session = Depends(get_db)
) -> Response:
    exp = db.query(Export).filter(Export.id == export_id).first()
    if not exp or not exp.master_asset_id:
        raise NotFoundError("Export file is not ready for download yet")
    ast = db.query(Asset).filter(Asset.id == exp.master_asset_id).first()
    if not ast or ast.project_id != exp.project_id:
        raise NotFoundError("Export asset missing")

    filename = f"kinetiq_{exp.project_id}_{exp.resolution}.mp4"
    return await asset_response(db, request, ast, filename=filename, inline=inline)

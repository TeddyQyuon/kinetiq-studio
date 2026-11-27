from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import NotFoundError
from app.models import Project, TimelineRevision
from app.timeline.schemas import CommandBatchRequest, TimelineDocument, TimelinePutRequest
from app.timeline.service import (
    apply_timeline_commands, get_latest_timeline, invalidate_changed_speech, save_timeline_document,
)

router = APIRouter(prefix="/v1/projects/{project_id}", tags=["timeline"])


@router.get("/timeline", response_model=TimelineDocument)
def read_timeline(project_id: str, db: Session = Depends(get_db)) -> TimelineDocument:
    return get_latest_timeline(db, project_id)


@router.put("/timeline", response_model=TimelineDocument)
def put_timeline(
    project_id: str,
    body: TimelinePutRequest,
    if_match: str | None = Header(default=None, alias="If-Match"),
    db: Session = Depends(get_db),
) -> TimelineDocument:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError(f"Project {project_id} not found")
    expected = int(if_match) if (if_match and if_match.isdigit()) else body.expected_revision
    invalidate_changed_speech(get_latest_timeline(db, project_id), body.document)
    return save_timeline_document(db, project, expected, body.document, author=body.author)


@router.post("/timeline/commands", response_model=TimelineDocument)
def post_commands(
    project_id: str,
    body: CommandBatchRequest,
    if_match: str | None = Header(default=None, alias="If-Match"),
    db: Session = Depends(get_db),
) -> TimelineDocument:
    expected = int(if_match) if (if_match and if_match.isdigit()) else body.expected_revision
    return apply_timeline_commands(db, project_id, expected, body.commands, author=body.author, dispatch_jobs=True)


@router.get("/revisions")
def list_revisions(project_id: str, db: Session = Depends(get_db)) -> list[dict]:
    rows = (
        db.query(TimelineRevision)
        .filter(TimelineRevision.project_id == project_id)
        .order_by(TimelineRevision.revision.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "revision": r.revision,
            "author": r.author,
            "commands_count": len(r.command_batch or []),
            "drive_snapshot_id": r.drive_snapshot_id,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.post("/revisions/{revision}/restore", response_model=TimelineDocument)
def restore_revision(project_id: str, revision: int, db: Session = Depends(get_db)) -> TimelineDocument:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError(f"Project {project_id} not found")
    target = (
        db.query(TimelineRevision)
        .filter(TimelineRevision.project_id == project_id, TimelineRevision.revision == revision)
        .first()
    )
    if not target:
        raise NotFoundError(f"Revision {revision} not found")
    doc = TimelineDocument.model_validate(target.document)
    return save_timeline_document(db, project, project.current_revision, doc, author="user")

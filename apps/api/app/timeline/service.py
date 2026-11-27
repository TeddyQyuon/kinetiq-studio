from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.events.service import emit_event
from app.models import Project, Scene, TimelineRevision
from app.timeline.commands import apply_command
from app.timeline.schemas import TimelineCommand, TimelineDocument


def get_latest_timeline(db: Session, project_id: str) -> TimelineDocument:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError(f"Project {project_id} not found")
    rev = (
        db.query(TimelineRevision)
        .filter(
            TimelineRevision.project_id == project_id,
            TimelineRevision.revision == project.current_revision,
        )
        .first()
    )
    if not rev:
        raise NotFoundError(f"Timeline revision {project.current_revision} missing for {project_id}")
    return TimelineDocument.model_validate(rev.document)


def sync_scenes_projection(db: Session, project_id: str, doc: TimelineDocument) -> None:
    existing = {s.id: s for s in db.query(Scene).filter(Scene.project_id == project_id).all()}
    seen_ids: set[str] = set()

    for idx, item in enumerate(doc.tracks.visual):
        seen_ids.add(item.id)
        row = existing.get(item.id)
        if row:
            row.order_index = idx
            row.start_ms = item.start_ms
            row.end_ms = item.end_ms
            row.kind = item.kind
            row.status = item.status
            if item.qa:
                row.qa_score = item.qa.score
                row.qa_attempts = item.qa.attempts
            if item.assets:
                row.keyframe_asset_id = item.assets.keyframe
                row.master_asset_id = item.assets.master
                row.proxy_asset_id = item.assets.proxy
                row.upscaled_asset_id = item.assets.upscaled_4k
        else:
            db.add(
                Scene(
                    id=item.id,
                    project_id=project_id,
                    order_index=idx,
                    start_ms=item.start_ms,
                    end_ms=item.end_ms,
                    kind=item.kind,
                    status=item.status,
                    qa_score=item.qa.score if item.qa else None,
                    qa_attempts=item.qa.attempts if item.qa else 0,
                    keyframe_asset_id=item.assets.keyframe if item.assets else None,
                    master_asset_id=item.assets.master if item.assets else None,
                    proxy_asset_id=item.assets.proxy if item.assets else None,
                    upscaled_asset_id=item.assets.upscaled_4k if item.assets else None,
                )
            )

    for sid, row in existing.items():
        if sid not in seen_ids:
            db.delete(row)


def save_timeline_document(
    db: Session,
    project: Project,
    expected_revision: int,
    new_doc: TimelineDocument,
    *,
    author: str = "user",
    command_batch: list[dict] | None = None,
) -> TimelineDocument:
    if project.current_revision != expected_revision:
        raise ConflictError(
            f"Revision conflict: expected {expected_revision}, server is at {project.current_revision}",
            details={"server_revision": project.current_revision},
        )
    new_rev = project.current_revision + 1
    new_doc.revision = new_rev
    project.current_revision = new_rev

    db.add(
        TimelineRevision(
            project_id=project.id,
            revision=new_rev,
            document=new_doc.model_dump(mode="json"),
            author=author,
            command_batch=command_batch or [],
        )
    )
    sync_scenes_projection(db, project.id, new_doc)
    emit_event(
        db,
        "timeline.updated",
        {"revision": new_rev, "author": author, "scene_count": len(new_doc.tracks.visual)},
        project_id=project.id,
    )
    db.commit()
    return new_doc


def apply_timeline_commands(
    db: Session,
    project_id: str,
    expected_revision: int,
    commands: list[TimelineCommand],
    *,
    author: str = "user",
) -> TimelineDocument:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError(f"Project {project_id} not found")
    if project.current_revision != expected_revision:
        raise ConflictError(
            f"Revision conflict: expected {expected_revision}, server is at {project.current_revision}",
            details={"server_revision": project.current_revision},
        )

    doc = get_latest_timeline(db, project_id)
    current = doc
    for cmd in commands:
        current, _ = apply_command(current, cmd)

    # save_timeline_document increments revision from project.current_revision once for the batch
    return save_timeline_document(
        db,
        project,
        expected_revision,
        current,
        author=author,
        command_batch=[c.model_dump(mode="json") for c in commands],
    )

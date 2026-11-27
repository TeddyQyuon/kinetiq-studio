from __future__ import annotations

import uuid

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.events.service import emit_event
from app.models import Project, Scene, TimelineRevision
from app.projects.speech import clear_scene_voice, enqueue_dependency, prepare_scene_speech, visual_status
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
    def conflict() -> ConflictError:
        return ConflictError(
            f"Revision conflict: expected {expected_revision}, server is at {project.current_revision}",
            details={"server_revision": project.current_revision},
        )

    if project.current_revision != expected_revision:
        raise conflict()
    new_doc.revision = expected_revision + 1
    db.add(
        TimelineRevision(
            project_id=project.id, revision=new_doc.revision, document=new_doc.model_dump(mode="json"),
            author=author, command_batch=command_batch or [],
        )
    )
    # Keep clip completion and the revision in the same flush for the QA gate.
    # All writers acquire the outbox lock before changing the project revision.
    with db.no_autoflush:
        sync_scenes_projection(db, project.id, new_doc)
        try:
            emit_event(
                db, "timeline.updated",
                {"revision": new_doc.revision, "author": author, "scene_count": len(new_doc.tracks.visual)},
                project_id=project.id,
            )
        except IntegrityError:
            db.rollback()
            if project.current_revision != expected_revision:
                raise conflict() from None
            raise
    changed = db.execute(
        update(Project)
        .where(Project.id == project.id, Project.current_revision == expected_revision)
        .values(current_revision=new_doc.revision)
    )
    if changed.rowcount != 1:
        db.rollback()
        raise conflict()
    db.commit()
    return new_doc


def dispatch_jobs_for_commands(
    db: Session, project: Project, doc: TimelineDocument, commands: list[TimelineCommand],
) -> list[str]:
    scenes = {s.id: s for s in doc.tracks.visual}
    jobs = []
    # Coalesce repeated edits in a batch; only its final scene inputs are visible.
    requests = dict.fromkeys(
        (cmd.type, cmd.scene_id) for cmd in commands if cmd.type in {"edit_narration", "regenerate_clip"}
    )
    for kind, scene_id in requests:
        scene = scenes.get(scene_id)
        if scene is None:
            continue
        if kind == "edit_narration":
            if scene.narration.strip():
                jobs.extend(j.id for j in prepare_scene_speech(db, doc, scene))
            else:
                clear_scene_voice(doc, scene)
                if scene.status == "generating_audio":
                    scene.status = visual_status(scene)
        else:
            job = enqueue_dependency(
                db, project.id, scene.id, "keyframe_qwen", {"seed": scene.seed, "aspect": doc.format.aspect},
                est_cu=0.02, salt=uuid.uuid4().hex[:6],
            )
            jobs.append(job.id)
    return jobs


def invalidate_changed_speech(previous: TimelineDocument, current: TimelineDocument) -> None:
    old_scenes = {s.id: s for s in previous.tracks.visual}
    for scene in current.tracks.visual:
        old = old_scenes.get(scene.id)
        if old and (old.narration != scene.narration or previous.voice != current.voice):
            clear_scene_voice(current, scene)
            if scene.status == "generating_audio":
                scene.status = visual_status(scene)


def apply_timeline_commands(
    db: Session,
    project_id: str,
    expected_revision: int,
    commands: list[TimelineCommand],
    *,
    author: str = "user",
    dispatch_jobs: bool = False,
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

    invalidate_changed_speech(doc, current)
    if dispatch_jobs:
        dispatch_jobs_for_commands(db, project, current, commands)

    # save_timeline_document increments revision from project.current_revision once for the batch
    return save_timeline_document(
        db,
        project,
        expected_revision,
        current,
        author=author,
        command_batch=[c.model_dump(mode="json") for c in commands],
    )

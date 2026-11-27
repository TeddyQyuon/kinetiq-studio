"""Publish immutable timeline history and recover the latest project from its vault."""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections import defaultdict
from contextlib import closing, contextmanager
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, UnauthorizedError, ValidationFailedError
from app.events.service import emit_event
from app.models import OAuthCredential, Project, TimelineRevision, User
from app.storage.drive import get_storage_provider
from app.storage.local import validate_upload_key
from app.timeline.schemas import SceneAssetRefs, SceneQASummary, TimelineDocument
from app.timeline.service import sync_scenes_projection

logger = logging.getLogger(__name__)


class _OwnerMetadata(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    display_name: str
    created_at: datetime


class _ProjectMetadata(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    owner_id: str
    title: str
    topic: str
    aspect: str
    target_res: str
    duration_preset: str
    qa_policy: str
    brain_mode: str
    status: str
    drive_folder_id: str | None
    current_revision: int
    created_at: datetime
    updated_at: datetime


class _RevisionMetadata(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    author: str
    command_batch: list
    created_at: datetime
    drive_snapshot_id: str | None


class _ProjectSnapshot(BaseModel):
    project: _ProjectMetadata
    owner: _OwnerMetadata
    revision_meta: _RevisionMetadata
    document: dict


_memory_snapshot_lock = threading.Lock()


@contextmanager
def _snapshot_lock(db: Session, project_id: str):
    """Serialize vault writes/restores without blocking normal timeline edits."""
    if db.get_bind().dialect.name == "postgresql":
        # Two-key variant gives snapshot locks their own advisory lock space.
        yield db.scalar(text("SELECT pg_try_advisory_xact_lock(1481790288, hashtext(:id))"), {"id": project_id})
        return
    filename = db.connection().exec_driver_sql("PRAGMA database_list").first()[2]
    if not filename:
        acquired = _memory_snapshot_lock.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                _memory_snapshot_lock.release()
        return
    # This SQLite sidecar coordinates even separate processes on Windows. A
    # crashed process releases its lock. No studio tables live in this file.
    with closing(sqlite3.connect(filename + ".snapshots.sqlite", timeout=0)) as lock:
        try:
            lock.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if exc.sqlite_errorcode != sqlite3.SQLITE_BUSY:
                raise
            yield False
        else:
            yield True


async def _publish_locked(db, provider, client, project_id, owner_id, revision_ids):
    project = db.get(Project, project_id, populate_existing=True)
    if project is None or project.owner_id != owner_id:
        return 0
    pending = db.scalars(
        select(TimelineRevision)
        .where(
            TimelineRevision.project_id == project_id,
            TimelineRevision.id.in_(revision_ids),
            TimelineRevision.drive_snapshot_id.is_(None),
        )
        .order_by(TimelineRevision.revision)
        .execution_options(populate_existing=True)
    ).all()
    if not pending:
        return 0

    folder = await provider.snapshot_folder(client, project) if provider.name == "drive" else None
    file_ids = {}
    for rev in pending:
        file_ids[rev.id] = await provider.write_json(
            client,
            f"{project_id}/history/v{rev.revision}.json",
            rev.document,
            project_folder_id=folder,
            revision=rev.revision,
        )

    # History can take several remote requests. Re-read the head after uploads;
    # regular edits stay available and later revisions remain pending for a tick.
    project = db.get(Project, project_id, populate_existing=True)
    if not project or project.owner_id != owner_id:
        return 0
    if folder and project.drive_folder_id not in (None, project_id, folder):
        return 0
    latest = db.scalars(
        select(TimelineRevision).where(
            TimelineRevision.project_id == project_id,
            TimelineRevision.revision == project.current_revision,
        )
    ).one()
    manifest = _ProjectSnapshot(
        project=_ProjectMetadata.model_validate(project),
        owner=_OwnerMetadata.model_validate(db.get(User, owner_id)),
        revision_meta=_RevisionMetadata.model_validate(latest),
        document=latest.document,
    )
    if folder:
        manifest.project.drive_folder_id = folder
    if latest.id in file_ids:
        manifest.revision_meta.drive_snapshot_id = file_ids[latest.id]
    await provider.write_json(
        client,
        f"{project_id}/project.json",
        manifest.model_dump(mode="json"),
        project_folder_id=folder,
        revision=latest.revision,
    )

    # No ORM changes until all external writes have succeeded.
    if folder and project.drive_folder_id != folder:
        db.execute(
            update(Project)
            .where(
                Project.id == project_id,
                Project.drive_folder_id == project.drive_folder_id,
            )
            .values(drive_folder_id=folder, updated_at=Project.updated_at)
        )
    for rev in pending:
        rev.drive_snapshot_id = file_ids[rev.id]
        emit_event(
            db,
            "vault.snapshot_saved",
            {"revision": rev.revision, "drive_snapshot_id": rev.drive_snapshot_id},
            project_id=project_id,
        )
    return len(pending)


async def _snapshot_project(db, provider, project_id, owner_id, revision_ids):
    validate_upload_key(f"{project_id}/project.json", project_id, "project.json")
    async with provider.snapshot_access(owner_id) as client:
        with _snapshot_lock(db, project_id) as acquired:
            if not acquired:
                return 0
            saved = await _publish_locked(db, provider, client, project_id, owner_id, revision_ids)
            db.commit()
            return saved


async def snapshot_pending_revisions(db: Session, limit: int = 20) -> int:
    if limit < 1:
        return 0
    provider = get_storage_provider(db)
    query = (
        select(Project.id, Project.owner_id, TimelineRevision.id)
        .join(TimelineRevision, TimelineRevision.project_id == Project.id)
        .where(TimelineRevision.drive_snapshot_id.is_(None))
        .order_by(TimelineRevision.created_at, TimelineRevision.id)
    )
    if provider.name == "drive":
        # Skip unconnected owners before applying the limit, without making
        # requests or allowing their revisions to starve connected owners.
        query = query.where(
            select(OAuthCredential.id)
            .where(
                OAuthCredential.user_id == Project.owner_id,
                OAuthCredential.provider == "google",
            )
            .exists()
        )
    attempted = set()
    saved = 0
    while saved < limit:
        page = query.where(Project.id.not_in(attempted)).limit(limit - saved)
        batches = defaultdict(list)
        for project_id, owner_id, revision_id in db.execute(page):
            batches[project_id, owner_id].append(revision_id)
        if not batches:
            break
        for (project_id, owner_id), revision_ids in batches.items():
            attempted.add(project_id)
            try:
                saved += await _snapshot_project(db, provider, project_id, owner_id, revision_ids)
            except (httpx.HTTPError, OSError, UnauthorizedError):
                db.rollback()
                logger.warning("Vault snapshot for %s will be retried", project_id, exc_info=True)
            except BaseException:
                db.rollback()
                raise
    return saved


def _aware(timestamp: datetime) -> datetime:
    # SQLite stores UTC datetimes without a timezone.
    return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)


def _restore(db, snapshot, project_id, user_id):
    timeline = TimelineDocument.model_validate(snapshot.document)
    metadata = snapshot.project
    if metadata.owner_id != user_id or snapshot.owner.id != user_id:
        raise UnauthorizedError("Vault snapshot belongs to a different owner")
    if metadata.id != project_id or timeline.project_id != project_id or timeline.revision != metadata.current_revision:
        raise ValidationFailedError("Vault project and timeline identity or revision disagree")

    # Only the short, database-only upsert needs to exclude concurrent edits
    # to an existing project. A no-op UPDATE works on SQLite and PostgreSQL.
    db.execute(update(Project).where(Project.id == project_id).values(updated_at=Project.updated_at))
    project = db.get(Project, project_id, populate_existing=True)
    if project and project.owner_id != user_id:
        raise NotFoundError("Project is not owned by this user")
    if project and project.current_revision > timeline.revision:
        raise ConflictError("Vault snapshot is older than the database revision")
    if db.get(User, user_id) is None:
        db.add(User(**snapshot.owner.model_dump()))
        db.flush()
    if project is None:
        project = Project(**metadata.model_dump())
        db.add(project)
    else:
        if _aware(project.updated_at) <= _aware(metadata.updated_at):
            for key, value in metadata.model_dump(exclude={"id", "owner_id"}).items():
                setattr(project, key, value)
        project.current_revision = timeline.revision
    db.flush()

    revision = db.scalars(
        select(TimelineRevision).where(
            TimelineRevision.project_id == project_id,
            TimelineRevision.revision == timeline.revision,
        )
    ).one_or_none()
    if revision is None:
        db.add(
            TimelineRevision(
                **snapshot.revision_meta.model_dump(),
                project_id=project_id,
                revision=timeline.revision,
                document=snapshot.document,
            )
        )
    elif revision.document != snapshot.document:
        raise ConflictError("Database and vault contain different documents for this revision")
    else:
        revision.author = snapshot.revision_meta.author
        revision.command_batch = snapshot.revision_meta.command_batch
        revision.created_at = snapshot.revision_meta.created_at
        if snapshot.revision_meta.drive_snapshot_id:
            revision.drive_snapshot_id = snapshot.revision_meta.drive_snapshot_id
    # The normal incremental projection preserves existing refs when a field
    # is absent. Recovery also clears stale refs in partially restored rows.
    for scene in timeline.tracks.visual:
        scene.assets = scene.assets or SceneAssetRefs()
        scene.qa = scene.qa or SceneQASummary()
    sync_scenes_projection(db, project_id, timeline)
    return project


async def rebuild_project_from_vault(db: Session, *, project_id: str, user_id: str) -> Project:
    validate_upload_key(f"{project_id}/project.json", project_id, "project.json")
    provider = get_storage_provider(db)
    try:
        async with provider.snapshot_access(user_id) as client:
            with _snapshot_lock(db, project_id) as acquired:
                if not acquired:
                    raise ConflictError("Another vault operation is in progress; retry recovery")
                snapshot = _ProjectSnapshot.model_validate(await provider.read_project_json(client, project_id))
                project = _restore(db, snapshot, project_id, user_id)
                db.commit()
                return project
    except (ValidationError, ValueError) as exc:
        db.rollback()
        raise ValidationFailedError("Invalid vault project snapshot") from exc
    except BaseException:
        db.rollback()
        raise

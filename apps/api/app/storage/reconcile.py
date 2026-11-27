from __future__ import annotations

import logging

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, UnauthorizedError, ValidationFailedError
from app.events.service import emit_event
from app.models import Asset, Project
from app.storage.drive import get_storage_provider

logger = logging.getLogger(__name__)


async def inspect_asset(db: Session, asset: Asset, owner: str) -> tuple[str, str]:
    try:
        provider = get_storage_provider(db, asset.storage_provider)
        meta = await provider.metadata(owner, asset.storage_key, asset.drive_file_id, checksum=bool(asset.sha256))
    except NotFoundError:
        return "missing", "file_missing"
    except ValidationFailedError:
        return "mismatch", "invalid_storage_reference"
    if asset.size_bytes is not None and str(meta.get("size")) != str(asset.size_bytes):
        return "mismatch", "size"
    props = meta.get("appProperties") or {}
    if asset.storage_provider == "drive":
        expected = {"project_id": asset.project_id, "kind": asset.kind, "scene_id": asset.scene_id or ""}
        if any(props.get(k, "" if k == "scene_id" else None) != v for k, v in expected.items()):
            return "mismatch", "appProperties"
    if asset.sha256 and any(
        value and value.lower() != asset.sha256.lower() for value in (meta.get("sha256Checksum"), props.get("sha256"))
    ):
        return "mismatch", "sha256"
    return "synced", "verified"


async def reconcile_vault(db: Session) -> int:
    cursor = ""
    changed = 0
    while True:
        page = db.execute(
            select(Asset, Project.owner_id)
            .join(Project, Project.id == Asset.project_id)
            .where(Asset.id > cursor)
            .order_by(Asset.id)
            .limit(100)
        ).all()
        if not page:
            return changed
        for asset, owner in page:
            cursor = asset.id
            try:
                state, reason = await inspect_asset(db, asset, owner)
            except (httpx.HTTPError, OSError, UnauthorizedError):
                logger.warning("Could not verify asset %s, will retry", asset.id, exc_info=True)
                continue
            if state == asset.sync_state:
                continue
            old = asset.sync_state
            claimed = db.execute(
                update(Asset).where(Asset.id == asset.id, Asset.sync_state == old).values(sync_state=state)
            )
            if claimed.rowcount:
                emit_event(
                    db,
                    "asset.reconciled",
                    {"asset_id": asset.id, "previous_state": old, "sync_state": state, "reason": reason},
                    project_id=asset.project_id,
                )
                changed += 1
            db.commit()

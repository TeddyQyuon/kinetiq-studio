from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.crypto import verify_media_token
from app.core.db import get_db
from app.core.errors import NotFoundError, UnauthorizedError
from app.models import Asset, Project
from app.storage.drive import get_storage_provider
from app.storage.local import put_chunk, vault_path

router = APIRouter(prefix="/v1", tags=["storage"])


@router.put("/storage/uploads/{token}")
async def upload_chunk(token: str, request: Request):
    data = bytearray()
    async for part in request.stream():
        if len(data) + len(part) > 8 * 1024 * 1024:
            raise HTTPException(413, "Chunk exceeds 8 MiB")
        data.extend(part)
    code, headers = await asyncio.to_thread(put_chunk, token, request.headers.get("Content-Range", ""), data)
    return Response(status_code=code, headers=headers)


@router.get("/media/{asset_id}")
async def media(asset_id: str, t: str, request: Request, db: Session = Depends(get_db)):
    if not t.isascii() or not verify_media_token(asset_id, t):
        raise UnauthorizedError("Invalid or expired media token")
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise NotFoundError("Asset missing")
    if asset.storage_provider == "local":
        path = vault_path(asset.storage_key)
        if not path.is_file():
            raise NotFoundError("Vault file missing")
        return FileResponse(path, media_type=asset.content_type)
    provider = get_storage_provider(db, asset.storage_provider)
    owner = db.get(Project, asset.project_id).owner_id
    start, end = 0, None
    byte_range = request.headers.get("range")
    if byte_range:
        meta = await provider.metadata(owner, asset.storage_key, asset.drive_file_id)
        size = int(meta["size"])
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", byte_range)
        if not match or not any(match.groups()):
            raise HTTPException(416, headers={"Content-Range": f"bytes */{size}"})
        first, last = match.groups()
        start = int(first) if first else max(0, size - int(last))
        end = min(int(last), size - 1) if first and last else size - 1
        if start > end:
            raise HTTPException(416, headers={"Content-Range": f"bytes */{size}"})
    data, total, ctype = await provider.read_range(
        user_id=owner, storage_key=asset.storage_key, drive_file_id=asset.drive_file_id, start=start, end=end
    )
    headers = {"Accept-Ranges": "bytes"}
    if byte_range:
        headers["Content-Range"] = f"bytes {start}-{end}/{total}"
    return Response(data, status_code=206 if byte_range else 200, media_type=ctype, headers=headers)

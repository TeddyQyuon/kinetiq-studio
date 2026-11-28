from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.crypto import verify_media_token
from app.core.db import get_db
from app.core.errors import NotFoundError, UnauthorizedError
from app.models import Asset
from app.storage.downloads import asset_response
from app.storage.local import put_chunk

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
    return await asset_response(db, request, asset)

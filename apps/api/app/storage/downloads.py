"""Serve a single byte range from the vault that owns an asset."""

from __future__ import annotations

import asyncio
import re
import stat
from urllib.parse import quote

from anyio import CancelScope
from fastapi import HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session
from starlette.types import Receive, Scope, Send

from app.core.errors import NotFoundError
from app.models import Asset, Project
from app.storage.drive import LocalVaultProvider, get_storage_provider
from app.storage.local import vault_path


def _requested_range(request: Request, size: int) -> tuple[int, int] | None:
    values = request.headers.getlist("range")
    if not values:
        return None

    invalid = HTTPException(416, headers={"Accept-Ranges": "bytes", "Content-Range": f"bytes */{size}"})
    match = re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", ",".join(values), re.IGNORECASE)
    if not match or not any(match.groups()) or size == 0:
        raise invalid
    first, last = match.groups()
    try:
        if first:
            start = int(first)
            end = min(int(last), size - 1) if last else size - 1
        else:
            start = max(0, size - int(last))
            end = size - 1
    except ValueError:  # Very long decimal fields can exceed Python's integer conversion limit.
        raise invalid from None
    if start > end:
        raise invalid
    return start, end


class _VaultStreamingResponse(StreamingResponse):
    async def stream_response(self, send: Send) -> None:
        try:
            # Open the vault file before committing the downstream status and headers.
            chunk = await anext(self.body_iterator, None)
            await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})
            while chunk is not None:
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
                chunk = await anext(self.body_iterator, None)
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            # A disconnect can interrupt the downstream send while the provider generator is suspended.
            with CancelScope(shield=True):
                await self.body_iterator.aclose()


class _LocalFileResponse(FileResponse):
    """Use native file sends when available, with cancellable reads on other ASGI servers."""

    def __init__(self, path, *, asset: Asset, request: Request, stat_result, filename, headers):
        media_type = asset.content_type
        if filename and media_type == "application/octet-stream":
            media_type = None  # Infer the MP4 type from the download name for older export rows.
        super().__init__(path, stat_result=stat_result, media_type=media_type, filename=filename, headers=headers)
        self.storage_key = asset.storage_key
        self.span = None
        size = stat_result.st_size
        span = _requested_range(request, size)
        if_range = request.headers.get("if-range")
        if span is not None and if_range in (None, self.headers["etag"], self.headers["last-modified"]):
            start, end = span
            self.span = (start, end)
            self.status_code = 206
            self.headers["Content-Length"] = str(end - start + 1)
            self.headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.span is None and "http.response.pathsend" in scope.get("extensions", {}):
            await super().__call__(scope, receive, send)
            return
        start, end = self.span or (0, None)
        chunks = LocalVaultProvider().stream_range(None, self.storage_key, None, start=start, end=end)
        await _VaultStreamingResponse(
            chunks, status_code=self.status_code, headers=self.headers, background=self.background
        )(scope, receive, send)


async def asset_response(
    db: Session, request: Request, asset: Asset, *, filename: str | None = None, inline: bool = False
) -> Response:
    headers = {"Accept-Ranges": "bytes"}
    if filename is not None:
        disposition = "inline" if inline else "attachment"
        encoded = quote(filename, safe="")
        name = f'filename="{filename}"' if encoded == filename else f"filename*=utf-8''{encoded}"
        headers["Content-Disposition"] = f"{disposition}; {name}"

    if asset.storage_provider == "local":
        path = vault_path(asset.storage_key)
        try:
            info = await asyncio.to_thread(path.stat)
        except (FileNotFoundError, NotADirectoryError):
            raise NotFoundError("Vault file missing") from None
        if not stat.S_ISREG(info.st_mode):
            raise NotFoundError("Vault file missing")
        return _LocalFileResponse(path, asset=asset, request=request, stat_result=info, filename=filename, headers=headers)

    project = db.get(Project, asset.project_id)
    if project is None:
        raise NotFoundError("Asset project missing")
    provider = get_storage_provider(db, asset.storage_provider)
    owner = project.owner_id
    metadata = await provider.metadata(owner, asset.storage_key, asset.drive_file_id)
    size = int(metadata["size"])
    media_type = metadata.get("mimeType") or asset.content_type
    span = _requested_range(request, size)
    # No Drive representation validator is advertised, so If-Range requires a full response.
    if request.headers.get("if-range") is not None:
        span = None
    start, end = span if span is not None else (0, None)
    length = size
    if span is not None:
        length = end - start + 1
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(length)
    if size == 0:
        return StreamingResponse(iter(()), media_type=media_type, headers=headers)
    chunks = provider.stream_range(
        user_id=owner, storage_key=asset.storage_key, drive_file_id=asset.drive_file_id, start=start, end=end
    )
    return _VaultStreamingResponse(chunks, status_code=206 if span else 200, media_type=media_type, headers=headers)

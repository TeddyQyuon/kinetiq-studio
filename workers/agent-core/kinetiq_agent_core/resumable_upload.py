from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

CHUNK_ALIGNMENT = 256 * 1024  # 256 KiB mandatory Google Drive alignment
DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MiB (32 x 256 KiB)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _parse_range_end(range_header: str | None) -> int:
    """Parse 'bytes=0-8388607' or '0-8388607' -> 8388607."""
    if not range_header:
        return -1
    cleaned = range_header.replace("bytes=", "").strip()
    if "-" not in cleaned:
        return -1
    _, end_str = cleaned.split("-", 1)
    try:
        return int(end_str)
    except ValueError:
        return -1


def upload_file_resumable(
    file_path: Path,
    *,
    session_uri: str,
    upload_headers: dict[str, str],
    content_type: str,
    chunk_size_bytes: int = DEFAULT_CHUNK_SIZE,
    refresh_auth_fn: Callable[[], str] | None = None,
    max_retries: int = 5,
) -> dict[str, Any]:
    """Upload a file using Google Drive v3 Resumable Upload protocol (8 MiB 256-KiB-aligned chunks)."""
    total_bytes = file_path.stat().st_size
    if total_bytes == 0:
        raise ValueError(f"Cannot upload empty file: {file_path}")

    aligned_chunk = max(CHUNK_ALIGNMENT, (chunk_size_bytes // CHUNK_ALIGNMENT) * CHUNK_ALIGNMENT)
    headers = dict(upload_headers)
    offset = 0
    failures = 0
    probe = False
    with httpx.Client(timeout=120) as client, file_path.open("rb") as f:
        while True:
            if probe:
                content_range = f"bytes */{total_bytes}"
                chunk = b""
            else:
                f.seek(offset)
                chunk = f.read(aligned_chunk)
                content_range = f"bytes {offset}-{offset + len(chunk) - 1}/{total_bytes}"
            try:
                resp = client.put(
                    session_uri,
                    headers={**headers, "Content-Type": content_type, "Content-Range": content_range},
                    content=chunk,
                )
            except httpx.TransportError:
                if failures >= max_retries:
                    raise
                probe = True
            else:
                if resp.status_code in (200, 201):
                    meta = resp.json() if resp.content else {}
                    return {"drive_file_id": meta.get("id"), "size_bytes": total_bytes, "sha256": sha256_file(file_path)}
                if resp.status_code == 308:
                    acknowledged = _parse_range_end(resp.headers.get("Range")) + 1
                    if not 0 <= acknowledged <= total_bytes:
                        raise ValueError("Invalid resumable upload offset")
                    advanced = acknowledged > offset
                    offset = acknowledged
                    was_probe = probe
                    probe = offset == total_bytes
                    if advanced:
                        failures = 0
                        continue
                    if was_probe and not probe and failures < max_retries:
                        continue
                elif resp.status_code == 401 and refresh_auth_fn and failures < max_retries:
                    auth = refresh_auth_fn()
                    if not auth:
                        resp.raise_for_status()
                    headers["Authorization"] = auth
                    probe = True
                elif resp.status_code in {408, 409, 429} or resp.is_server_error:
                    probe = True
                else:
                    resp.raise_for_status()
                    raise RuntimeError(f"Unexpected upload response {resp.status_code}")

            failures += 1
            if failures > max_retries:
                raise RuntimeError("Resumable upload made no progress after retries")
            time.sleep(min(30, 2 ** failures))

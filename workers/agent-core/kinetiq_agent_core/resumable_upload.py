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


def _query_uploaded_offset(
    client: httpx.Client,
    session_uri: str,
    headers: dict[str, str],
    total_bytes: int,
) -> int:
    res = client.put(
        session_uri,
        headers={
            **headers,
            "Content-Length": "0",
            "Content-Range": f"bytes */{total_bytes}",
        },
    )
    if res.status_code == 308:
        end_byte = _parse_range_end(res.headers.get("Range"))
        return end_byte + 1 if end_byte >= 0 else 0
    if res.status_code in (200, 201):
        return total_bytes
    res.raise_for_status()
    return 0


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
    attempt = 0

    with httpx.Client(timeout=120) as client, open(file_path, "rb") as f:
        while offset < total_bytes:
            end = min(offset + aligned_chunk, total_bytes)
            f.seek(offset)
            chunk = f.read(end - offset)

            put_headers = {
                **headers,
                "Content-Type": content_type,
                "Content-Range": f"bytes {offset}-{end - 1}/{total_bytes}",
            }
            try:
                resp = client.put(session_uri, headers=put_headers, content=chunk)
                if resp.status_code == 401 and refresh_auth_fn is not None:
                    new_auth = refresh_auth_fn()
                    if new_auth:
                        headers["Authorization"] = new_auth
                        continue

                if resp.status_code == 308:
                    attempt = 0
                    range_end = _parse_range_end(resp.headers.get("Range"))
                    offset = (range_end + 1) if range_end >= 0 else end
                    continue

                if resp.status_code in (200, 201):
                    meta = resp.json() if resp.content else {}
                    return {
                        "drive_file_id": meta.get("id"),
                        "size_bytes": total_bytes,
                        "sha256": sha256_file(file_path),
                    }

                resp.raise_for_status()
            except (httpx.HTTPError, OSError):
                attempt += 1
                if attempt > max_retries:
                    raise
                time.sleep(min(30, 2 ** attempt))
                try:
                    offset = _query_uploaded_offset(client, session_uri, headers, total_bytes)
                except Exception:
                    pass

    return {"drive_file_id": None, "size_bytes": total_bytes, "sha256": sha256_file(file_path)}

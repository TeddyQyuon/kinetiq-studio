from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationFailedError


def vault_path(key: str) -> Path:
    root = Path(get_settings().local_vault_dir).resolve()
    if not key or Path(key).is_absolute() or "\x00" in key or ".." in Path(key).parts:
        raise ValidationFailedError("Invalid vault key")
    path = (root / key).resolve()
    if not path.is_relative_to(root) or path == root or ".uploads" in path.relative_to(root).parts:
        raise ValidationFailedError("Invalid vault key")
    return path


def validate_upload_key(key: str, project_id: str, filename: str) -> None:
    parts = key.split("/")
    if (
        parts[0] != project_id
        or parts[-1] != filename
        or "/" in filename
        or "\\" in key
        or any(p in {"", ".", "..", ".uploads"} or ":" in p or "\0" in p for p in parts)
    ):
        raise ValidationFailedError("Invalid upload key")


@contextmanager
def upload_registry():
    directory = Path(get_settings().local_vault_dir) / ".uploads"
    directory.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(directory / "sessions.sqlite", timeout=30)) as conn, conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS sessions
            (token TEXT PRIMARY KEY, key TEXT, size INTEGER, offset INTEGER, expires INTEGER)""")
        conn.execute("BEGIN IMMEDIATE")
        yield conn, directory


def start_upload(key: str, size: int) -> str:
    token = secrets.token_urlsafe(32)
    with upload_registry() as (conn, directory):
        now = int(time.time())
        for (expired,) in conn.execute("SELECT token FROM sessions WHERE expires <= ?", (now,)):
            (directory / expired).unlink(missing_ok=True)
        conn.execute("DELETE FROM sessions WHERE expires <= ?", (now,))
        conn.execute("INSERT INTO sessions VALUES (?, ?, ?, 0, ?)", (token, key, size, now + 86400))
    return token


def put_chunk(token: str, content_range: str, data: bytes) -> tuple[int, dict]:
    match = re.fullmatch(r"bytes (?:(\d+)-(\d+)|\*)/(\d+)", content_range)
    if not match:
        raise ValidationFailedError("Invalid Content-Range")
    start, end, total = match.groups()
    with upload_registry() as (conn, directory):
        row = conn.execute(
            "SELECT key, size, offset FROM sessions WHERE token=? AND expires>?", (token, int(time.time()))
        ).fetchone()
        if not row:
            raise NotFoundError("Upload session expired or missing")
        key, size, offset = row
        if int(total) != size:
            raise ValidationFailedError("Upload size changed")
        path = directory / token
        target = vault_path(key)
        if start is not None and offset < size:
            if int(start) != offset:
                raise ConflictError("Query the committed upload offset and retry")
            if not data or int(end) + 1 != offset + len(data) or int(end) >= size:
                raise ValidationFailedError("Chunk length does not match Content-Range")
            with path.open("r+b" if path.exists() else "w+b") as f:
                f.seek(offset)
                f.write(data)
                f.truncate()
            offset += len(data)
            conn.execute("UPDATE sessions SET offset=? WHERE token=?", (offset, token))
        if offset == size:
            # Persist the offset first. A retry can finish the atomic rename even
            # if the process exits between the final chunk and publication.
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            if path.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(path, target)
            return 201, {}
    return 308, {"Range": f"bytes=0-{offset - 1}"} if offset else {}


def local_metadata(key: str, checksum: bool = False) -> dict:
    path = vault_path(key)
    try:
        with path.open("rb") as f:
            size = os.fstat(f.fileno()).st_size
            digest = hashlib.file_digest(f, "sha256").hexdigest() if checksum else None
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        raise NotFoundError("Vault file missing") from None
    return {"size": size, "sha256Checksum": digest, "mimeType": mimetypes.guess_type(key)[0]}

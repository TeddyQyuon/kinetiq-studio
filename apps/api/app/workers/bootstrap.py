"""Provision a persistent local worker identity and its private token file."""

import argparse
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.crypto import generate_token, hash_token
from app.core.db import SessionLocal, init_db
from app.models import Worker


def _write_token(path: Path, token: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # mkstemp creates a private (0600) file on the same filesystem as the target.
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(token + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _ensure_schema() -> None:
    # create_all checks then creates each table. Concurrent first boots can
    # observe the same missing table before either initializer has finished.
    for attempt in range(6):
        try:
            init_db()
            return
        except DBAPIError as exc:
            cause = exc.orig
            sqlite_race = isinstance(cause, sqlite3.OperationalError) and (
                "already exists" in str(cause) or cause.sqlite_errorcode in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
            )
            pg_race = getattr(cause, "sqlstate", None) in {"23505", "42P07", "42710"}
            if attempt == 5 or not (sqlite_race or pg_race):
                raise
            time.sleep(0.05 * 2**attempt)


def bootstrap(name: str, profile: str, capabilities: list[str], token_file: Path, token: str | None = None) -> dict:
    if token and (not token.startswith("wrk_") or len(token) == 4 or any(c.isspace() for c in token)):
        raise ValueError("Supply a worker token beginning with wrk_ without whitespace.")
    _ensure_schema()
    with SessionLocal.begin() as db:
        # Worker.name predates this CLI and is not unique. Serialize lookup + insert,
        # including the first registration when there is no row to lock.
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:name))"), {"name": name})
        else:
            db.execute(text("BEGIN IMMEDIATE"))
        matches = db.scalars(select(Worker).where(Worker.name == name)).all()
        if len(matches) > 1:
            raise ValueError(f"Multiple workers named {name!r} exist. Give the local worker a unique name.")
        worker = matches[0] if matches else Worker(name=name)
        try:
            stored = token_file.read_text(encoding="utf-8").strip()
        except (FileNotFoundError, UnicodeDecodeError):
            stored = ""
        reusable = stored.startswith("wrk_") and hash_token(stored) == worker.token_hash
        selected = token or (stored if reusable else generate_token("wrk"))
        worker.provider = "local"
        worker.profile = profile
        worker.capabilities = capabilities
        worker.token_hash = hash_token(selected)
        db.add(worker)
        db.flush()
        if selected != stored:
            _write_token(token_file, selected)
        else:
            token_file.chmod(0o600)
        return {"worker_id": worker.id, "name": name, "profile": profile, "capabilities": capabilities,
                "token_file": str(token_file), "reused": bool(reusable and selected == stored)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--capabilities", required=True, help="Comma-separated job capabilities")
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--token", help="Use a supplied token (defaults to the profile's worker token environment variable)")
    parser.add_argument("--json", action="store_true", help="Print registration metadata as JSON")
    args = parser.parse_args()
    capabilities = list(dict.fromkeys(c.strip() for c in args.capabilities.split(",") if c.strip()))
    if not capabilities:
        parser.error("At least one capability is required.")
    env_key = {"media": "MEDIA_WORKER_TOKEN", "render": "RENDER_WORKER_TOKEN"}.get(args.profile.removesuffix("_local"))
    token = args.token or (os.getenv(env_key) if env_key else None)
    try:
        result = bootstrap(args.name, args.profile, capabilities, args.token_file, token)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result) if args.json else f"Worker {args.name!r} ready. Token file: {args.token_file}")


if __name__ == "__main__":
    main()

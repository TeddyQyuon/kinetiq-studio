from __future__ import annotations

import secrets

from fastapi import HTTPException, Request
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import hash_token
from app.core.db import SessionLocal
from app.core.errors import UnauthorizedError
from app.core.rate_limit import check_worker_rate
from app.identity.cookies import CSRF, SESSION, unseal
from app.models import OAuthCredential, Worker

# These endpoints independently authenticate their scoped worker or upload/media capability.
_MACHINE_ROUTES = {
    ("POST", "/v1/workers/register"),
    ("POST", "/v1/workers/heartbeat"),
    ("POST", "/v1/workers/jobs/claim"),
    *(
        ("POST", "/v1/workers/jobs/{job_id}/" + action)
        for action in ("progress", "upload-session", "upload-token-refresh", "complete", "fail")
    ),
    ("PUT", "/v1/storage/uploads/{token}"),
    ("GET", "/v1/media/{asset_id}"),
}


def require_access(request: Request) -> None:
    path = request.scope["route"].path
    method = request.method
    if not path.startswith("/v1/"):
        return
    if path == "/v1/workers" or path.startswith("/v1/workers/"):
        key = "ip:" + (request.client.host if request.client else "unknown")
        auth = request.headers.get("authorization", "")
        if (method, path) in _MACHINE_ROUTES and auth.startswith("Bearer "):
            with SessionLocal() as db:
                worker = db.scalar(select(Worker.id).where(Worker.token_hash == hash_token(auth[7:].strip())))
            if worker:
                key = "worker:" + worker
        check_worker_rate(key)

    if (method, path) in _MACHINE_ROUTES or path in {"/v1/auth/google/start", "/v1/auth/google/callback"}:
        return
    if get_settings().app_env != "phase1_vps":
        return
    value = unseal(SESSION, request.cookies.get(SESSION, ""))
    credential_id, csrf = value.split(".", 1)
    with SessionLocal() as db:
        cred = db.get(OAuthCredential, credential_id)
        if cred is None or cred.user_id != "usr_owner_local" or cred.provider != "google":
            raise UnauthorizedError("Owner session has ended")
    request.state.csrf_token = csrf
    if method not in {"GET", "HEAD", "OPTIONS"}:
        tokens = (request.cookies.get(CSRF, ""), request.headers.get("X-CSRF-Token", ""))
        if not all(t.isascii() and secrets.compare_digest(t, csrf) for t in tokens):
            raise HTTPException(403, "Invalid CSRF token")

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from fastapi import Response

from app.core.config import get_settings
from app.core.errors import UnauthorizedError

SESSION = "__Host-kinetiq-session"
CSRF = "__Host-kinetiq-csrf"
PKCE = "__Host-kinetiq-pkce"
SESSION_TTL = 12 * 3600
PKCE_TTL = 600


def _mac(purpose: str, value: str) -> str:
    key = get_settings().session_secret.encode()
    return hmac.new(key, f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()


def seal(purpose: str, value: str, ttl: int) -> str:
    message = f"{int(time.time()) + ttl}.{value}"
    return f"{message}.{_mac(purpose, message)}"


def unseal(purpose: str, cookie: str) -> str:
    try:
        message, signature = cookie.rsplit(".", 1)
        expiry, value = message.split(".", 1)
        if cookie.isascii() and hmac.compare_digest(signature, _mac(purpose, message)) and int(expiry) > time.time():
            return value
    except ValueError:
        pass
    raise UnauthorizedError("Invalid or expired cookie")


def issue_session(response: Response, credential_id: str) -> None:
    csrf = secrets.token_urlsafe(32)
    value = seal(SESSION, f"{credential_id}.{csrf}", SESSION_TTL)
    samesite = get_settings().cookie_samesite
    response.set_cookie(SESSION, value, max_age=SESSION_TTL, secure=True, httponly=True, samesite=samesite)
    response.set_cookie(CSRF, csrf, max_age=SESSION_TTL, secure=True, httponly=False, samesite=samesite)

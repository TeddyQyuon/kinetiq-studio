from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.errors import UnauthorizedError
from app.models import OAuthCredential, User

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
GOOGLE_SCOPES = "openid email https://www.googleapis.com/auth/drive.file"

# In-memory PKCE verifier store for Phase 0; short-lived state -> (verifier, created_at)
_PKCE_STATE_STORE: dict[str, tuple[str, datetime]] = {}


def ensure_default_owner(db: Session) -> User:
    user = db.query(User).filter(User.id == "usr_owner_local").first()
    if user:
        return user
    user = User(
        id="usr_owner_local",
        email="owner@kinetiq.local",
        display_name="Studio Owner",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def start_google_oauth() -> tuple[str, str]:
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        raise UnauthorizedError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are not configured in .env")

    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    _PKCE_STATE_STORE[state] = (verifier, datetime.now(timezone.utc))

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}", state


async def complete_google_oauth(db: Session, code: str, state: str) -> User:
    settings = get_settings()
    entry = _PKCE_STATE_STORE.pop(state, None)
    if not entry:
        raise UnauthorizedError("Invalid or expired OAuth state parameter")
    verifier, _ = entry

    async with httpx.AsyncClient(timeout=20) as client:
        tok_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "code": code,
                "code_verifier": verifier,
                "grant_type": "authorization_code",
                "redirect_uri": settings.google_redirect_uri,
            },
        )
        tok_resp.raise_for_status()
        tokens = tok_resp.json()

        access_token = tokens["access_token"]
        refresh_token = tokens.get("refresh_token")
        expires_in = int(tokens.get("expires_in", 3600))

        ui_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        ui_resp.raise_for_status()
        profile = ui_resp.json()

    user = ensure_default_owner(db)
    user.email = profile.get("email", user.email)
    user.google_sub = profile.get("sub")
    user.display_name = profile.get("name", user.display_name)

    cred = (
        db.query(OAuthCredential)
        .filter(OAuthCredential.user_id == user.id, OAuthCredential.provider == "google")
        .first()
    )
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    if cred:
        if refresh_token:
            cred.encrypted_refresh_token = encrypt_secret(refresh_token)
        cred.encrypted_access_token = encrypt_secret(access_token)
        cred.scopes = tokens.get("scope", GOOGLE_SCOPES)
        cred.expires_at = expires_at
    else:
        if not refresh_token:
            raise UnauthorizedError("Google did not return a refresh_token; revoke app access and retry with consent.")
        cred = OAuthCredential(
            user_id=user.id,
            provider="google",
            encrypted_refresh_token=encrypt_secret(refresh_token),
            encrypted_access_token=encrypt_secret(access_token),
            scopes=tokens.get("scope", GOOGLE_SCOPES),
            expires_at=expires_at,
        )
        db.add(cred)

    db.commit()
    db.refresh(user)
    return user


def has_google_drive_connected(db: Session, user_id: str) -> bool:
    cred = (
        db.query(OAuthCredential)
        .filter(OAuthCredential.user_id == user_id, OAuthCredential.provider == "google")
        .first()
    )
    return cred is not None


async def get_valid_google_access_token(db: Session, user_id: str) -> str:
    settings = get_settings()
    cred = (
        db.query(OAuthCredential)
        .filter(OAuthCredential.user_id == user_id, OAuthCredential.provider == "google")
        .first()
    )
    if not cred:
        raise UnauthorizedError("Google Drive is not connected yet. Connect via /v1/auth/google/start.")

    now = datetime.now(timezone.utc)
    if cred.encrypted_access_token and cred.expires_at and cred.expires_at > now + timedelta(seconds=90):
        return decrypt_secret(cred.encrypted_access_token)

    refresh_token = decrypt_secret(cred.encrypted_refresh_token)
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    access_token = data["access_token"]
    expires_in = int(data.get("expires_in", 3600))
    cred.encrypted_access_token = encrypt_secret(access_token)
    cred.expires_at = now + timedelta(seconds=expires_in)
    db.commit()
    return access_token

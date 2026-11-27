from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.identity.schemas import OAuthStartResponse, UserStatusResponse
from app.identity.service import (
    complete_google_oauth,
    ensure_default_owner,
    has_google_drive_connected,
    start_google_oauth,
)
from app.models import OAuthCredential

router = APIRouter(prefix="/v1", tags=["identity"])


@router.get("/me", response_model=UserStatusResponse)
def get_me(db: Session = Depends(get_db)) -> UserStatusResponse:
    settings = get_settings()
    user = ensure_default_owner(db)
    return UserStatusResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        drive_connected=has_google_drive_connected(db, user.id),
        storage_provider=settings.storage_provider,
        gemini_configured=bool(settings.gemini_api_key),
        openai_configured=bool(settings.openai_api_key),
        mcp_configured=bool(settings.mcp_access_token),
    )


@router.get("/auth/google/start", response_model=OAuthStartResponse)
def google_oauth_start() -> OAuthStartResponse:
    auth_url, state = start_google_oauth()
    return OAuthStartResponse(auth_url=auth_url, state=state)


@router.get("/auth/google/callback")
async def google_oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    settings = get_settings()
    await complete_google_oauth(db, code=code, state=state)
    return RedirectResponse(url=f"{settings.public_web_url}/settings?drive=connected")


@router.post("/auth/logout")
def logout(db: Session = Depends(get_db)) -> dict[str, bool]:
    user = ensure_default_owner(db)
    db.query(OAuthCredential).filter(OAuthCredential.user_id == user.id).delete()
    db.commit()
    return {"ok": True}

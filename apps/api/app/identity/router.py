from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.identity.cookies import CSRF, PKCE, PKCE_TTL, SESSION, issue_session
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
def get_me(request: Request, response: Response, db: Session = Depends(get_db)) -> UserStatusResponse:
    settings = get_settings()
    user = ensure_default_owner(db)
    response.headers["Cache-Control"] = "no-store"
    return UserStatusResponse(
        csrf_token=getattr(request.state, "csrf_token", None),
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
def google_oauth_start(response: Response) -> OAuthStartResponse:
    auth_url, state, cookie = start_google_oauth()
    response.headers["Cache-Control"] = "no-store"
    if cookie:
        response.set_cookie(PKCE, cookie, max_age=PKCE_TTL, httponly=True, secure=True, samesite="lax")
    return OAuthStartResponse(auth_url=auth_url, state=state)


@router.get("/auth/google/callback")
async def google_oauth_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    settings = get_settings()
    credential = await complete_google_oauth(db, code=code, state=state, cookie=request.cookies.get(PKCE, ""))
    response = RedirectResponse(url=f"{settings.public_web_url}/settings?drive=connected")
    if settings.app_env == "phase1_vps":
        response.delete_cookie(PKCE, secure=True, httponly=True)
        issue_session(response, credential.id)
    return response


@router.post("/auth/logout")
def logout(response: Response, db: Session = Depends(get_db)) -> dict[str, bool]:
    user = ensure_default_owner(db)
    db.query(OAuthCredential).filter(OAuthCredential.user_id == user.id).delete()
    db.commit()
    for name in (SESSION, CSRF):
        response.delete_cookie(name, secure=True, httponly=name == SESSION, samesite="lax")
    return {"ok": True}

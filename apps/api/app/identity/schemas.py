from __future__ import annotations

from pydantic import BaseModel


class UserStatusResponse(BaseModel):
    user_id: str
    email: str
    display_name: str
    drive_connected: bool
    storage_provider: str
    gemini_configured: bool
    openai_configured: bool
    mcp_configured: bool


class OAuthStartResponse(BaseModel):
    auth_url: str
    state: str

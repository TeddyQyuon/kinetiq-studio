from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "phase0_local"
    run_embedded_orchestrator: bool = True

    database_url: str = "sqlite:///./kinetiq.db"
    redis_url: str = ""

    encryption_master_key: str = "dev-only-32-byte-base64-key-change-before-prod"
    session_secret: str = "dev-only-session-secret-change-me"
    media_signing_secret: str = "dev-only-media-hmac-secret-change-me"
    mcp_access_token: str = ""

    storage_provider: Literal["local", "drive"] = "local"
    local_vault_dir: str = "./storage/vault"

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/v1/auth/google/callback"

    gemini_api_key: str = ""
    gemini_pro_model: str = "gemini-2.5-pro"
    gemini_flash_model: str = "gemini-2.5-flash"
    openai_api_key: str = ""

    qa_default_policy: Literal["hotl_smart", "hitl_strict_gate", "full_auto"] = "hotl_smart"
    qa_pass_threshold: float = 8.0
    qa_max_retries: int = 2

    job_lease_seconds: int = 300
    worker_offline_after_seconds: int = 60

    public_api_url: str = "http://localhost:8000"
    public_web_url: str = "http://localhost:3000"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"])


@lru_cache
def get_settings() -> Settings:
    return Settings()

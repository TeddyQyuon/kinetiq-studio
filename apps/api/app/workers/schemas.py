from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class WorkerTokenCreateRequest(BaseModel):
    name: str
    provider: Literal["google_colab", "runpod", "local"] = "google_colab"
    profile: Literal["ltx25_pro", "wan22_fast", "upscale_4k", "media", "render"] = "ltx25_pro"


class WorkerTokenCreateResponse(BaseModel):
    worker_id: str
    name: str
    profile: str
    worker_token: str
    capabilities: list[str]


class WorkerRead(BaseModel):
    id: str
    name: str
    provider: str
    profile: str
    gpu_model: str | None
    vram_gb: int | None
    capabilities: list[str]
    status: str
    last_heartbeat: datetime | None
    cu_used_month: float


class WorkerRegisterRequest(BaseModel):
    provider: Literal["google_colab", "runpod", "local"] = "google_colab"
    profile: str = "ltx25_pro"
    gpu_model: str | None = None
    vram_gb: int | None = None
    capabilities: list[str] = Field(default_factory=list)


class WorkerRegisterResponse(BaseModel):
    worker_id: str
    name: str
    capabilities: list[str]


class WorkerHeartbeatRequest(BaseModel):
    worker_id: str


class JobClaimRequest(BaseModel):
    worker_id: str
    capabilities: list[str]


class JobClaimResponse(BaseModel):
    id: str
    project_id: str
    scene_id: str | None
    job_type: str
    capability: str
    payload: dict
    input_download_url: str | None = None
    input_filename: str | None = None
    comfy_graph: dict | None = None


class JobProgressRequest(BaseModel):
    percent: int = Field(ge=0, le=100)
    step: int | None = None
    total_steps: int | None = None


class UploadSessionRequest(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"
    total_bytes: int = Field(ge=1)
    asset_kind: str = "master"


class UploadSessionResponse(BaseModel):
    provider: str
    storage_key: str
    session_uri: str
    upload_headers: dict[str, str]
    chunk_size_bytes: int
    token_refresh_url: str | None = None


class UploadedArtifactInfo(BaseModel):
    asset_kind: str
    storage_key: str
    drive_file_id: str | None = None
    content_type: str = "application/octet-stream"
    size_bytes: int
    sha256: str | None = None
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None


class JobCompleteRequest(BaseModel):
    artifacts: list[UploadedArtifactInfo]
    gpu_seconds: float = 0.0
    result_meta: dict = Field(default_factory=dict)


class JobFailRequest(BaseModel):
    error: str

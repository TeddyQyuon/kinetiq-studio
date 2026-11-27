from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel


class JobRead(BaseModel):
    id: str
    project_id: str
    scene_id: str | None
    job_type: str
    capability: str
    status: str
    attempts: int
    max_attempts: int
    progress_pct: int
    gpu_seconds: float | None
    est_cu: float | None
    error_message: str | None
    created_at: datetime

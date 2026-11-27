from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class QAScores(BaseModel):
    narration_match: float = Field(ge=0.0, le=10.0)
    artifact_free: float = Field(ge=0.0, le=10.0)
    style_consistency: float = Field(ge=0.0, le=10.0)
    safe_zone: float = Field(ge=0.0, le=10.0)
    reason: str
    suggested_fix: str | None = None
    prefer_diagram: bool = False


class QAReviewRead(BaseModel):
    id: str
    scene_id: str
    asset_id: str | None
    narration_match: float
    artifact_free: float
    style_consistency: float
    safe_zone: float
    score: float
    verdict: str
    reason: str
    fix_action: str | None
    created_at: datetime

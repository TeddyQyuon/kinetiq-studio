from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field

from app.timeline.schemas import TimelineDocument


class ProjectCreateRequest(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    topic: str = Field(min_length=3)
    aspect: Literal["9:16", "16:9"] = "16:9"
    target_res: Literal["720p", "1080p", "4k"] = "1080p"
    duration_preset: Literal["reel_60s", "lesson_5m", "lesson_10m"] = "reel_60s"
    qa_policy: Literal["hotl_smart", "hitl_strict_gate", "full_auto"] = "hotl_smart"
    brain_mode: Literal["gemini_auto", "chatgpt_bridge", "openai_api"] = "gemini_auto"
    script_override: str | None = None


class ProjectUpdateRequest(BaseModel):
    title: str | None = None
    qa_policy: Literal["hotl_smart", "hitl_strict_gate", "full_auto"] | None = None
    target_res: Literal["720p", "1080p", "4k"] | None = None


class ProjectSummaryRead(BaseModel):
    id: str
    title: str
    topic: str
    aspect: str
    target_res: str
    duration_preset: str
    qa_policy: str
    brain_mode: str
    status: str
    drive_folder_id: str | None
    current_revision: int
    scene_count: int
    ready_scenes: int
    needs_review_count: int
    created_at: datetime
    updated_at: datetime


class ProjectDetailRead(ProjectSummaryRead):
    timeline: TimelineDocument
    media_urls: dict[str, str]


class SceneRegenerateRequest(BaseModel):
    prompt: str | None = None
    model: Literal["ltx25_fp8", "wan22_i2v", "qwen_image"] | None = None
    camera: Literal["static", "slow_dolly_in", "aerial_pullback", "cinematic_orbit", "macro_pan"] | None = None
    seed: int | None = None

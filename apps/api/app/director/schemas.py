from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field

from app.timeline.schemas import DiagramProps, TimelineCommand, TimelineDocument


class PlannedSceneSpec(BaseModel):
    narration: str
    duration_ms: int = Field(default=6000, ge=2500, le=18000)
    kind: Literal["ai_video", "diagram", "image_motion"]
    camera: Literal["static", "slow_dolly_in", "aerial_pullback", "cinematic_orbit", "macro_pan"] = "slow_dolly_in"
    model: Literal["ltx25_fp8", "wan22_i2v", "qwen_image"] = "ltx25_fp8"
    ltx25_prompt: str = ""
    wan22_prompt: str = ""
    qwen_image_prompt: str = ""
    diagram_template: Literal["BarCompare", "StepBreakdown", "TimelineFlow", "StatCallout"] | None = None
    diagram_props: DiagramProps | None = None


class PlannedStoryboard(BaseModel):
    palette: list[str]
    look: str
    negative: str
    hook: str
    seo_title: str
    description: str
    hashtags: list[str]
    scenes: list[PlannedSceneSpec]


class PlanRequest(BaseModel):
    script_override: str | None = None
    brain_mode: Literal["gemini_auto", "chatgpt_bridge", "openai_api", "colab_ai"] | None = None


class ChatGPTImportRequest(BaseModel):
    raw_json: str


class CopilotChatRequest(BaseModel):
    message: str
    selected_scene_id: str | None = None
    playhead_ms: int = 0


class CopilotProposalResponse(BaseModel):
    proposal_id: str
    assistant_message: str
    commands: list[TimelineCommand]
    affected_scenes: list[str]
    est_cu: float
    requires_confirmation: bool
    status: Literal["pending", "accepted", "rejected"]


class CopilotAcceptRequest(BaseModel):
    expected_revision: int
    confirm_gpu_spend: bool = False


class DirectorBriefResponse(BaseModel):
    project_id: str
    instructions: str
    schema_hint: dict
    timeline_preview: TimelineDocument | None = None

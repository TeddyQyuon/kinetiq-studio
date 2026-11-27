from __future__ import annotations

from typing import Annotated, Literal, Union
from pydantic import BaseModel, Field


class TimelineFormat(BaseModel):
    aspect: Literal["9:16", "16:9"]
    fps: Literal[24, 30, 60] = 30
    target: Literal["720p", "1080p", "4k"] = "1080p"
    width: int
    height: int


class StyleBible(BaseModel):
    palette: list[str]
    look: str
    negative: str
    safe_zone: Literal["9x16_reels", "16x9_standard"]


class VoiceConfig(BaseModel):
    engine: Literal["kokoro", "qwen3_tts"] = "kokoro"
    voice_id: str = "am_michael"
    speed: float = Field(default=1.0, ge=0.7, le=1.4)


class ScenePromptBundle(BaseModel):
    ltx25: str | None = None
    wan22: str | None = None
    qwen_image: str | None = None


class SceneAssetRefs(BaseModel):
    keyframe: str | None = None
    master: str | None = None
    proxy: str | None = None
    upscaled_4k: str | None = None


class SceneQASummary(BaseModel):
    score: float | None = None
    attempts: int = 0
    verdict: Literal["pending", "passed", "auto_healing", "needs_review", "human_approved"] = "pending"
    reason: str | None = None


class DiagramProps(BaseModel):
    title: str
    subtitle: str | None = None
    labels: list[str] | None = None
    values: list[float] | None = None
    unit: str | None = None
    steps: list[str] | None = None
    highlight_index: int | None = None
    stat_value: str | None = None
    stat_label: str | None = None


class VisualSceneItem(BaseModel):
    id: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    source_start_ms: int = Field(default=0, ge=0)
    kind: Literal["ai_video", "diagram", "image_motion"]
    narration: str
    prompt: ScenePromptBundle | None = None
    camera: Literal["static", "slow_dolly_in", "aerial_pullback", "cinematic_orbit", "macro_pan"] | None = "slow_dolly_in"
    model: Literal["ltx25_fp8", "wan22_i2v", "qwen_image"] | None = "ltx25_fp8"
    seed: int | None = None
    template: Literal["BarCompare", "StepBreakdown", "TimelineFlow", "StatCallout"] | None = None
    props: DiagramProps | None = None
    assets: SceneAssetRefs | None = None
    qa: SceneQASummary | None = None
    status: Literal[
        "planned",
        "generating_audio",
        "generating_keyframe",
        "qa_checking",
        "needs_review",
        "generating_clip",
        "ready",
        "failed",
    ] = "planned"


class VoiceClipItem(BaseModel):
    scene_id: str
    asset: str | None = None
    start_ms: int
    end_ms: int


class WordTiming(BaseModel):
    word: str
    start_ms: int
    end_ms: int


class CaptionTrackConfig(BaseModel):
    enabled: bool = True
    style: Literal["kinetic_highlight", "clean_subtitle", "bold_reels"] = "kinetic_highlight"
    accent_color: str = "#F59E0B"
    position: Literal["bottom", "center_lower"] = "bottom"
    words_asset: str | None = None
    words: list[WordTiming] | None = None


class MusicTrackConfig(BaseModel):
    mood: str = "documentary_ambient"
    asset: str | None = None
    duck_db: float = -26.0
    volume: float = 0.25


class OverlayItem(BaseModel):
    id: str
    type: Literal["callout", "badge", "chapter_title"]
    start_ms: int
    end_ms: int
    text: str
    position: Literal["top", "center", "bottom"] | None = "top"


class TimelineTracks(BaseModel):
    visual: list[VisualSceneItem]
    voice: list[VoiceClipItem] = Field(default_factory=list)
    captions: CaptionTrackConfig = Field(default_factory=CaptionTrackConfig)
    music: MusicTrackConfig = Field(default_factory=MusicTrackConfig)
    overlays: list[OverlayItem] = Field(default_factory=list)


class TimelineDocument(BaseModel):
    schema_version: Literal[1] = 1
    project_id: str
    revision: int = Field(default=1, ge=1)
    format: TimelineFormat
    style_bible: StyleBible
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    tracks: TimelineTracks


# --- Command Unions ---

class TrimSceneCmd(BaseModel):
    type: Literal["trim_scene"]
    scene_id: str
    start_ms: int
    end_ms: int
    source_start_ms: int | None = Field(default=None, ge=0)


class SplitSceneCmd(BaseModel):
    type: Literal["split_scene"]
    scene_id: str
    split_at_ms: int
    new_scene_id: str


class MoveSceneCmd(BaseModel):
    type: Literal["move_scene"]
    scene_id: str
    to_index: int


class DeleteSceneCmd(BaseModel):
    type: Literal["delete_scene"]
    scene_id: str


class RestoreSceneCmd(BaseModel):
    type: Literal["restore_scene"]
    scene: VisualSceneItem
    index: int


class TightenPausesCmd(BaseModel):
    type: Literal["tighten_pauses"]
    max_gap_ms: int = 120


class EditNarrationCmd(BaseModel):
    type: Literal["edit_narration"]
    scene_id: str
    narration: str


class RegenerateClipCmd(BaseModel):
    type: Literal["regenerate_clip"]
    scene_id: str
    prompt: str | None = None
    model: Literal["ltx25_fp8", "wan22_i2v", "qwen_image"] | None = None
    camera: Literal["static", "slow_dolly_in", "aerial_pullback", "cinematic_orbit", "macro_pan"] | None = None
    seed: int | None = None


class ApproveSceneQACmd(BaseModel):
    type: Literal["approve_scene_qa"]
    scene_id: str


class ConvertSceneToDiagramCmd(BaseModel):
    type: Literal["convert_scene_to_diagram"]
    scene_id: str
    template: Literal["BarCompare", "StepBreakdown", "TimelineFlow", "StatCallout"]
    props: DiagramProps


class InsertDiagramCmd(BaseModel):
    type: Literal["insert_diagram"]
    at_ms: int
    duration_ms: int
    scene_id: str
    narration: str
    template: Literal["BarCompare", "StepBreakdown", "TimelineFlow", "StatCallout"]
    props: DiagramProps


class AddOverlayCmd(BaseModel):
    type: Literal["add_overlay"]
    overlay: OverlayItem


class RemoveOverlayCmd(BaseModel):
    type: Literal["remove_overlay"]
    overlay_id: str


class UpdateCaptionsCmd(BaseModel):
    type: Literal["update_captions"]
    patch: dict


class MixAudioCmd(BaseModel):
    type: Literal["mix_audio"]
    mood: str | None = None
    duck_db: float | None = None
    volume: float | None = None
    voice_speed: float | None = None


TimelineCommand = Annotated[
    Union[
        TrimSceneCmd,
        SplitSceneCmd,
        MoveSceneCmd,
        DeleteSceneCmd,
        RestoreSceneCmd,
        TightenPausesCmd,
        EditNarrationCmd,
        RegenerateClipCmd,
        ApproveSceneQACmd,
        ConvertSceneToDiagramCmd,
        InsertDiagramCmd,
        AddOverlayCmd,
        RemoveOverlayCmd,
        UpdateCaptionsCmd,
        MixAudioCmd,
    ],
    Field(discriminator="type"),
]


class CommandBatchRequest(BaseModel):
    expected_revision: int
    author: Literal["user", "ai", "mcp"] = "user"
    commands: list[TimelineCommand]


class TimelinePutRequest(BaseModel):
    expected_revision: int
    document: TimelineDocument
    author: Literal["user", "ai", "mcp"] = "user"

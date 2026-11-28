export type AspectRatio = "9:16" | "16:9";
export type TargetResolution = "720p" | "1080p" | "4k";
export type QAPolicy = "hotl_smart" | "hitl_strict_gate" | "full_auto";
export type BrainMode = "gemini_auto" | "chatgpt_bridge" | "openai_api" | "colab_ai";

export type SceneKind = "ai_video" | "diagram" | "image_motion";
export type SceneStatus =
  | "planned"
  | "generating_audio"
  | "generating_keyframe"
  | "qa_checking"
  | "needs_review"
  | "generating_clip"
  | "ready"
  | "failed";

export type DiagramTemplate = "BarCompare" | "StepBreakdown" | "TimelineFlow" | "StatCallout";
export type CameraPreset =
  | "static"
  | "slow_dolly_in"
  | "aerial_pullback"
  | "cinematic_orbit"
  | "macro_pan";

export type ModelChoice = "ltx25_fp8" | "wan22_i2v" | "qwen_image";

export interface TimelineFormat {
  aspect: AspectRatio;
  fps: 24 | 30 | 60;
  target: TargetResolution;
  width: number;
  height: number;
}

export interface StyleBible {
  palette: string[];
  look: string;
  negative: string;
  safe_zone: "9x16_reels" | "16x9_standard";
}

export interface VoiceConfig {
  engine: "kokoro" | "qwen3_tts";
  voice_id: string;
  speed: number;
}

export interface ScenePromptBundle {
  ltx25?: string;
  wan22?: string;
  qwen_image?: string;
}

export interface SceneAssetRefs {
  keyframe?: string | null;
  master?: string | null;
  proxy?: string | null;
  upscaled_4k?: string | null;
}

export interface SceneQASummary {
  score: number | null;
  attempts: number;
  verdict: "pending" | "passed" | "auto_healing" | "needs_review" | "human_approved";
  reason?: string | null;
}

export interface DiagramProps {
  title: string;
  subtitle?: string;
  labels?: string[];
  values?: number[];
  unit?: string;
  steps?: string[];
  highlight_index?: number;
  stat_value?: string;
  stat_label?: string;
}

export interface VisualSceneItem {
  id: string;
  start_ms: number;
  end_ms: number;
  source_start_ms?: number;
  kind: SceneKind;
  narration: string;
  prompt?: ScenePromptBundle;
  camera?: CameraPreset;
  model?: ModelChoice;
  seed?: number;
  template?: DiagramTemplate;
  props?: DiagramProps;
  assets?: SceneAssetRefs;
  qa?: SceneQASummary;
  status: SceneStatus;
}

export interface VoiceClipItem {
  scene_id: string;
  asset: string | null;
  start_ms: number;
  end_ms: number;
}

export interface WordTiming {
  word: string;
  start_ms: number;
  end_ms: number;
}

export interface CaptionTrackConfig {
  enabled: boolean;
  style: "kinetic_highlight" | "clean_subtitle" | "bold_reels";
  accent_color: string;
  position: "bottom" | "center_lower";
  words_asset?: string | null;
  words?: WordTiming[];
}

export interface MusicTrackConfig {
  mood: string;
  asset: string | null;
  duck_db: number;
  volume: number;
}

export interface OverlayItem {
  id: string;
  type: "callout" | "badge" | "chapter_title";
  start_ms: number;
  end_ms: number;
  text: string;
  position?: "top" | "center" | "bottom";
}

export interface TimelineTracks {
  visual: VisualSceneItem[];
  voice: VoiceClipItem[];
  captions: CaptionTrackConfig;
  music: MusicTrackConfig;
  overlays: OverlayItem[];
}

export interface TimelineDocument {
  schema_version: 1;
  project_id: string;
  revision: number;
  format: TimelineFormat;
  style_bible: StyleBible;
  voice: VoiceConfig;
  tracks: TimelineTracks;
}

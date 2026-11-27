import type {
  AspectRatio,
  BrainMode,
  QAPolicy,
  TargetResolution,
  TimelineDocument,
} from "./timeline";
import type { TimelineCommand } from "./commands";

export interface ProjectSummary {
  id: string;
  title: string;
  topic: string;
  aspect: AspectRatio;
  target_res: TargetResolution;
  duration_preset: "reel_60s" | "lesson_5m" | "lesson_10m";
  qa_policy: QAPolicy;
  brain_mode: BrainMode;
  status: string;
  drive_folder_id: string | null;
  current_revision: number;
  scene_count: number;
  ready_scenes: number;
  needs_review_count: number;
  created_at: string;
  updated_at: string;
}

export interface ProjectDetail extends ProjectSummary {
  timeline: TimelineDocument;
  media_urls: Record<string, string>;
}

export interface QAReviewRecord {
  id: string;
  scene_id: string;
  asset_id: string | null;
  narration_match: number;
  artifact_free: number;
  style_consistency: number;
  safe_zone: number;
  score: number;
  verdict: string;
  reason: string;
  fix_action: string | null;
  created_at: string;
}

export interface JobRecord {
  id: string;
  project_id: string;
  scene_id: string | null;
  job_type: string;
  capability: string;
  status: "queued" | "leased" | "running" | "succeeded" | "failed" | "dead";
  attempts: number;
  max_attempts: number;
  progress_pct: number;
  gpu_seconds: number | null;
  est_cu: number | null;
  error_message: string | null;
  created_at: string;
}

export interface WorkerRecord {
  id: string;
  name: string;
  provider: "google_colab" | "runpod" | "local";
  profile: string;
  gpu_model: string | null;
  vram_gb: number | null;
  capabilities: string[];
  status: "online" | "busy" | "offline";
  last_heartbeat: string | null;
  cu_used_month: number;
}

export interface CopilotProposal {
  proposal_id: string;
  assistant_message: string;
  commands: TimelineCommand[];
  affected_scenes: string[];
  est_cu: number;
  requires_confirmation: boolean;
  status: "pending" | "accepted" | "rejected";
}

export interface PublishKit {
  hook: string;
  seo_title: string;
  description: string;
  hashtags: string[];
  ai_disclosure_note: string;
}

export interface ExportRecord {
  id: string;
  project_id: string;
  revision: number;
  resolution: TargetResolution;
  status: "queued" | "rendering" | "ready" | "failed";
  download_url: string | null;
  srt_url: string | null;
  drive_file_id: string | null;
  publish_kit: PublishKit;
  created_at: string;
}

export interface StudioEvent {
  id: number;
  project_id: string | null;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface DirectorBriefExport {
  project_id: string;
  instructions: string;
  schema_hint: Record<string, unknown>;
}

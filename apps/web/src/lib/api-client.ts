import type {
  BrainMode,
  CopilotProposal,
  DirectorBriefExport,
  ExportRecord,
  JobRecord,
  ProjectDetail,
  ProjectSummary,
  QAPolicy,
  QAReviewRecord,
  TargetResolution,
  TimelineCommand,
  TimelineDocument,
  WorkerRecord,
} from "@kinetiq/contracts";
import { DEMO_EXPORTS, DEMO_PROJECTS, DEMO_QA_REVIEWS, DEMO_WORKERS } from "./demo-data";

export const API_BASE = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");

export interface IdentityStatus {
  user_id: string;
  email: string;
  display_name: string;
  drive_connected: boolean;
  storage_provider: string;
  gemini_configured: boolean;
  openai_configured: boolean;
  mcp_configured: boolean;
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const DEMO_IDENTITY: IdentityStatus = {
  user_id: "usr_demo",
  email: "creator@kinetiq.local",
  display_name: "Demo Studio (API Offline)",
  drive_connected: false,
  storage_provider: "local",
  gemini_configured: false,
  openai_configured: false,
  mcp_configured: false,
};

function cloneData<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function isDemoProjectId(id: string): boolean {
  return DEMO_PROJECTS.some((p) => p.id === id);
}

const BACKEND_CACHE_TTL_MS = 10_000;
let cachedBackendOnline: { online: boolean; checkedAt: number } | null = null;
let inflightBackendCheck: Promise<boolean> | null = null;

export async function checkBackendOnline(forceRefresh = false): Promise<boolean> {
  if (typeof window === "undefined") {
    return true;
  }
  const now = Date.now();
  if (!forceRefresh && cachedBackendOnline && now - cachedBackendOnline.checkedAt < BACKEND_CACHE_TTL_MS) {
    return cachedBackendOnline.online;
  }
  if (!forceRefresh && inflightBackendCheck) {
    return inflightBackendCheck;
  }

  inflightBackendCheck = (async () => {
    try {
      const res = await fetch("/api/backend-status", { cache: "no-store" });
      if (!res.ok) {
        cachedBackendOnline = { online: false, checkedAt: Date.now() };
        return false;
      }
      const data = (await res.json()) as { online?: boolean };
      const online = Boolean(data.online);
      cachedBackendOnline = { online, checkedAt: Date.now() };
      return online;
    } catch {
      cachedBackendOnline = { online: false, checkedAt: Date.now() };
      return false;
    } finally {
      inflightBackendCheck = null;
    }
  })();

  return inflightBackendCheck;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<{ data: T; demo: boolean }> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const errText = await res.text().catch(() => res.statusText);
    throw new ApiError(errText || `HTTP ${res.status}`, res.status);
  }
  const data = (await res.json()) as T;
  return { data, demo: false };
}

export const api = {
  async getIdentity(): Promise<{ data: IdentityStatus; demo: boolean }> {
    if (!(await checkBackendOnline())) {
      return { data: cloneData(DEMO_IDENTITY), demo: true };
    }
    try {
      return await requestJson<IdentityStatus>("/v1/me");
    } catch {
      return { data: cloneData(DEMO_IDENTITY), demo: true };
    }
  },

  async listProjects(): Promise<{ data: ProjectSummary[]; demo: boolean }> {
    if (!(await checkBackendOnline())) {
      return { data: cloneData(DEMO_PROJECTS), demo: true };
    }
    try {
      return await requestJson<ProjectSummary[]>("/v1/projects");
    } catch {
      return { data: cloneData(DEMO_PROJECTS), demo: true };
    }
  },

  async getProject(id: string): Promise<{ data: ProjectDetail; demo: boolean }> {
    if (!(await checkBackendOnline())) {
      const found = DEMO_PROJECTS.find((p) => p.id === id) ?? DEMO_PROJECTS[0];
      return { data: cloneData(found), demo: true };
    }
    try {
      return await requestJson<ProjectDetail>(`/v1/projects/${id}`);
    } catch {
      const found = DEMO_PROJECTS.find((p) => p.id === id) ?? DEMO_PROJECTS[0];
      return { data: cloneData(found), demo: true };
    }
  },

  async createProject(payload: {
    title: string;
    topic: string;
    aspect: "9:16" | "16:9";
    target_res: TargetResolution;
    duration_preset: "reel_60s" | "lesson_5m" | "lesson_10m";
    qa_policy: QAPolicy;
    brain_mode: BrainMode;
    script_override?: string;
  }): Promise<{ data: ProjectDetail; demo: boolean }> {
    if (!(await checkBackendOnline())) {
      const demoProj: ProjectDetail = {
        ...cloneData(DEMO_PROJECTS[0]),
        id: `prj_demo_${Date.now().toString(36)}`,
        title: payload.title,
        topic: payload.topic,
        aspect: payload.aspect,
        target_res: payload.target_res,
        duration_preset: payload.duration_preset,
        qa_policy: payload.qa_policy,
        brain_mode: payload.brain_mode,
        status: "planned",
      };
      return { data: demoProj, demo: true };
    }
    try {
      return await requestJson<ProjectDetail>("/v1/projects", {
        method: "POST",
        body: JSON.stringify(payload),
      });
    } catch {
      const demoProj: ProjectDetail = {
        ...cloneData(DEMO_PROJECTS[0]),
        id: `prj_demo_${Date.now().toString(36)}`,
        title: payload.title,
        topic: payload.topic,
        aspect: payload.aspect,
        target_res: payload.target_res,
        duration_preset: payload.duration_preset,
        qa_policy: payload.qa_policy,
        brain_mode: payload.brain_mode,
        status: "planned",
      };
      return { data: demoProj, demo: true };
    }
  },

  async getChatGPTBrief(projectId: string): Promise<DirectorBriefExport> {
    if (!(await checkBackendOnline())) {
      return {
        project_id: projectId,
        instructions:
          "Act as an elite faceless educational video director. Return ONLY valid JSON matching PlannedStoryboard with alternating 'ai_video', 'diagram', and 'image_motion' scenes.",
        schema_hint: {
          required: ["palette", "look", "negative", "hook", "seo_title", "scenes"],
        },
      };
    }
    try {
      return (await requestJson<DirectorBriefExport>(`/v1/projects/${projectId}/plan/brief`)).data;
    } catch {
      return {
        project_id: projectId,
        instructions:
          "Act as an elite faceless educational video director. Return ONLY valid JSON matching PlannedStoryboard with alternating 'ai_video', 'diagram', and 'image_motion' scenes.",
        schema_hint: {
          required: ["palette", "look", "negative", "hook", "seo_title", "scenes"],
        },
      };
    }
  },

  async importChatGPTPlan(projectId: string, rawJson: string): Promise<TimelineDocument> {
    return (
      await requestJson<TimelineDocument>(`/v1/projects/${projectId}/plan/import`, {
        method: "POST",
        body: JSON.stringify({ raw_json: rawJson }),
      })
    ).data;
  },

  async startGeneration(projectId: string): Promise<{ queued_jobs: number }> {
    return (
      await requestJson<{ queued_jobs: number }>(`/v1/projects/${projectId}/generate`, {
        method: "POST",
      })
    ).data;
  },

  async listQAReviews(projectId: string): Promise<QAReviewRecord[]> {
    if (!(await checkBackendOnline())) {
      return cloneData(DEMO_QA_REVIEWS);
    }
    try {
      return (await requestJson<QAReviewRecord[]>(`/v1/projects/${projectId}/qa-reviews`)).data;
    } catch {
      return cloneData(DEMO_QA_REVIEWS);
    }
  },

  async approveScene(projectId: string, sceneId: string): Promise<void> {
    await requestJson(`/v1/projects/${projectId}/scenes/${sceneId}/approve`, { method: "POST" });
  },

  async regenerateScene(
    projectId: string,
    sceneId: string,
    body: { prompt?: string; model?: string; camera?: string },
  ): Promise<{ ok: boolean; revision: number; queued_job_id: string }> {
    return (
      await requestJson<{ ok: boolean; revision: number; queued_job_id: string }>(
        `/v1/projects/${projectId}/scenes/${sceneId}/regenerate`,
        {
          method: "POST",
          body: JSON.stringify(body),
        },
      )
    ).data;
  },

  async applyCommands(
    projectId: string,
    expectedRevision: number,
    commands: TimelineCommand[],
  ): Promise<TimelineDocument> {
    return (
      await requestJson<TimelineDocument>(`/v1/projects/${projectId}/timeline/commands`, {
        method: "POST",
        headers: { "If-Match": String(expectedRevision) },
        body: JSON.stringify({ expected_revision: expectedRevision, author: "user", commands }),
      })
    ).data;
  },

  async putTimeline(
    projectId: string,
    expectedRevision: number,
    document: TimelineDocument,
  ): Promise<TimelineDocument> {
    return (
      await requestJson<TimelineDocument>(`/v1/projects/${projectId}/timeline`, {
        method: "PUT",
        headers: { "If-Match": String(expectedRevision) },
        body: JSON.stringify({ expected_revision: expectedRevision, author: "user", document }),
      })
    ).data;
  },

  async copilotChat(
    projectId: string,
    message: string,
    selectedSceneId?: string | null,
  ): Promise<CopilotProposal> {
    if (!(await checkBackendOnline())) {
      const target = selectedSceneId || "scn_003";
      return {
        proposal_id: `prop_demo_${Date.now().toString(36)}`,
        assistant_message: "Demo Copilot: Prepared a 1-click preview edit for your timeline.",
        commands: [
          { type: "tighten_pauses", max_gap_ms: 80 },
          { type: "regenerate_clip", scene_id: target, camera: "cinematic_orbit" },
        ],
        affected_scenes: [target],
        est_cu: 0.08,
        requires_confirmation: false,
        status: "pending",
      };
    }
    try {
      return (
        await requestJson<CopilotProposal>(`/v1/projects/${projectId}/copilot`, {
          method: "POST",
          body: JSON.stringify({ message, selected_scene_id: selectedSceneId ?? null }),
        })
      ).data;
    } catch {
      const target = selectedSceneId || "scn_003";
      return {
        proposal_id: `prop_demo_${Date.now().toString(36)}`,
        assistant_message: "Demo Copilot: Prepared a 1-click preview edit for your timeline.",
        commands: [
          { type: "tighten_pauses", max_gap_ms: 80 },
          { type: "regenerate_clip", scene_id: target, camera: "cinematic_orbit" },
        ],
        affected_scenes: [target],
        est_cu: 0.08,
        requires_confirmation: false,
        status: "pending",
      };
    }
  },

  async acceptCopilot(
    projectId: string,
    proposalId: string,
    expectedRevision: number,
    confirmGpuSpend: boolean,
  ): Promise<TimelineDocument> {
    return (
      await requestJson<TimelineDocument>(`/v1/projects/${projectId}/copilot/${proposalId}/accept`, {
        method: "POST",
        body: JSON.stringify({
          expected_revision: expectedRevision,
          confirm_gpu_spend: confirmGpuSpend,
        }),
      })
    ).data;
  },

  async listWorkers(): Promise<{ data: WorkerRecord[]; demo: boolean }> {
    if (!(await checkBackendOnline())) {
      return { data: cloneData(DEMO_WORKERS), demo: true };
    }
    try {
      return await requestJson<WorkerRecord[]>("/v1/workers");
    } catch {
      return { data: cloneData(DEMO_WORKERS), demo: true };
    }
  },

  async createWorkerToken(name: string, profile: string): Promise<{ worker_id: string; worker_token: string }> {
    return (
      await requestJson<{ worker_id: string; worker_token: string }>("/v1/workers/tokens", {
        method: "POST",
        body: JSON.stringify({ name, provider: "google_colab", profile }),
      })
    ).data;
  },

  async listJobs(projectId: string): Promise<JobRecord[]> {
    if (!(await checkBackendOnline())) {
      return [];
    }
    try {
      return (await requestJson<JobRecord[]>(`/v1/projects/${projectId}/jobs`)).data;
    } catch {
      return [];
    }
  },

  async listExports(projectId: string): Promise<{ data: ExportRecord[]; demo: boolean }> {
    if (!(await checkBackendOnline())) {
      const filtered = DEMO_EXPORTS.filter((e) => e.project_id === projectId);
      return {
        data: cloneData(filtered.length ? filtered : DEMO_EXPORTS),
        demo: true,
      };
    }
    try {
      return await requestJson<ExportRecord[]>(`/v1/projects/${projectId}/exports`);
    } catch {
      const filtered = DEMO_EXPORTS.filter((e) => e.project_id === projectId);
      return {
        data: cloneData(filtered.length ? filtered : DEMO_EXPORTS),
        demo: true,
      };
    }
  },

  async createExport(projectId: string, resolution: TargetResolution): Promise<ExportRecord> {
    return (
      await requestJson<ExportRecord>(`/v1/projects/${projectId}/exports`, {
        method: "POST",
        body: JSON.stringify({ resolution }),
      })
    ).data;
  },

  async startGoogleDriveAuth(): Promise<string> {
    const res = await requestJson<{ auth_url: string }>("/v1/auth/google/start");
    return res.data.auth_url;
  },
};
import {
  type BrainMode, type CopilotProposal, type DirectorBriefExport, type ExportRecord,
  type HealthMetricsResponse, type IdentityStatus, type JobRecord, type MusicUploadResponse,
  type ProjectDetail, type ProjectSummary, type QAAnalyticsResponse, type QAReviewRecord,
  type RevisionSummary, type TargetResolution, type TimelineCommand, type TimelineDocument, type WorkerRecord,
} from "@kinetiq/contracts";
import { DEMO_EXPORTS, DEMO_PROJECTS, DEMO_QA_REVIEWS } from "./demo-data";
import {
  cloneDemoProject, demoCommands, demoHistory, demoProject, demoWorkspace, importDemoStoryboard,
  persistDemo, saveDemoTimeline,
} from "./demo-workspace";

export type { IdentityStatus } from "@kinetiq/contracts";
export const API_BASE = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");
export const AUTH_REQUIRED_EVENT = "kinetiq:auth-required";

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

const BACKEND_CACHE_TTL_MS = 10_000;
const BACKEND_OFFLINE_CACHE_TTL_MS = 2_000;
let cachedBackendOnline: { online: boolean; checkedAt: number } | null = null;
let backendEnvironment: string | undefined;
let inflightBackendCheck: Promise<boolean> | null = null;

export async function checkBackendOnline(forceRefresh = false): Promise<boolean> {
  if (typeof window === "undefined") {
    return true;
  }
  const now = Date.now();
  if (
    !forceRefresh &&
    cachedBackendOnline &&
    now - cachedBackendOnline.checkedAt <
      (cachedBackendOnline.online ? BACKEND_CACHE_TTL_MS : BACKEND_OFFLINE_CACHE_TTL_MS)
  ) {
    return cachedBackendOnline.online;
  }
  if (!forceRefresh && inflightBackendCheck) {
    return inflightBackendCheck;
  }

  inflightBackendCheck = (async () => {
    try {
      const res = await fetch("/api/backend-status", { cache: "no-store", credentials: "include" });
      if (!res.ok) {
        cachedBackendOnline = { online: false, checkedAt: Date.now() };
        return false;
      }
      const data = (await res.json()) as { online?: boolean; env?: string };
      backendEnvironment = data.env;
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

type Result<T> = { data: T; demo: boolean };
type MusicOptions = { volume?: number; duck_db?: number; mood?: string };
export type MusicPreset = "ambient_pulse" | "warm_pad" | "documentary_minimal";

let csrfToken: string | null = null;
let refreshingIdentity: Promise<IdentityStatus> | null = null;

function cookieToken() {
  if (typeof document === "undefined") return null;
  const prefix = "__Host-kinetiq-csrf=";
  const cookie = document.cookie.split(/;\s*/).find(value => value.startsWith(prefix));
  return cookie?.slice(prefix.length) || null;
}

async function fetchJson<T>(path: string, init: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, credentials: "include", cache: "no-store" });
  } catch {
    cachedBackendOnline = { online: false, checkedAt: Date.now() };
    throw new ApiError("Connection to FastAPI was interrupted. Reload to check the latest state.", 0);
  }
  if (!res.ok) {
    if (res.status === 401) {
      csrfToken = null;
      if (typeof window !== "undefined") window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
    }
    const body = await res.json().catch(() => null);
    const message = body?.message ?? body?.detail;
    throw new ApiError(typeof message === "string" ? message : `Request failed (HTTP ${res.status}).`, res.status);
  }
  return res.status === 204 ? undefined as T : await res.json() as T;
}

function readIdentity() {
  refreshingIdentity ??= fetchJson<IdentityStatus>("/v1/me", {}).then(identity => {
    csrfToken = identity.csrf_token || null;
    return identity;
  }).finally(() => { refreshingIdentity = null; });
  return refreshingIdentity;
}

async function requestJson<T>(
  path: string, init: RequestInit = {}, fallback?: () => T,
): Promise<Result<T>> {
  if (!(await checkBackendOnline())) {
    if (fallback) return { data: structuredClone(fallback()), demo: true };
    throw new ApiError("Start FastAPI to use this action.", 0);
  }
  const headers = new Headers(init.headers);
  if (typeof init.body === "string") headers.set("Content-Type", "application/json");
  const writes = ["POST", "PUT", "PATCH", "DELETE"].includes(init.method ?? "GET");
  if (writes) {
    let token = csrfToken || cookieToken();
    if (!token && backendEnvironment === "phase1_vps") {
      await readIdentity();
      token = csrfToken || cookieToken();
      if (!token) throw new ApiError("Refresh your owner session to continue.", 403);
    }
    if (token) headers.set("X-CSRF-Token", token);
  }
  try {
    return { data: await fetchJson<T>(path, { ...init, headers }), demo: false };
  } catch (error) {
    // A lost mutation response can follow a successful commit. Only reads can
    // switch to a demo result after the request has been sent.
    if (!writes && fallback && error instanceof ApiError && error.status === 0) {
      return { data: structuredClone(fallback()), demo: true };
    }
    throw error;
  }
}

function mutation(method: string, body?: unknown, expectedRevision?: number): RequestInit {
  return {
    method, body: body === undefined ? undefined : JSON.stringify(body),
    headers: expectedRevision === undefined ? undefined : { "If-Match": String(expectedRevision) },
  };
}

const projectPath = (id: string) => `/v1/projects/${encodeURIComponent(id)}`;

function demoMusic(id: string, mood: string, options: MusicOptions, expectedRevision?: number): MusicUploadResponse {
  const timeline = structuredClone(demoProject(id).timeline);
  const asset = `ast_demo_${crypto.randomUUID()}`;
  timeline.tracks.music = { volume: options.volume ?? 0.25, duck_db: options.duck_db ?? -26, mood, asset };
  return { asset_id: asset, media_url: `mock://${asset}`, timeline: saveDemoTimeline(id, timeline, expectedRevision) };
}

export const api = {
  async getIdentity(): Promise<Result<IdentityStatus>> {
    if (!(await checkBackendOnline())) return { data: DEMO_IDENTITY, demo: true };
    try {
      return { data: await readIdentity(), demo: false };
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      if (err.status === 0) return { data: DEMO_IDENTITY, demo: true };
      if (err.status !== 401) throw err;
      return { data: { ...DEMO_IDENTITY, user_id: "", email: "", display_name: "", auth_required: true }, demo: false };
    }
  },

  listProjects(): Promise<Result<ProjectSummary[]>> {
    return requestJson("/v1/projects", {}, () => demoWorkspace().projects);
  },

  getProject(id: string): Promise<Result<ProjectDetail>> {
    return requestJson(projectPath(id), {}, () => demoProject(id));
  },

  createProject(payload: Pick<ProjectSummary, "title" | "topic" | "aspect" | "target_res" | "duration_preset" | "qa_policy" | "brain_mode"> & { script_override?: string }): Promise<Result<ProjectDetail>> {
    return requestJson("/v1/projects", mutation("POST", payload), () => {
      const template = DEMO_PROJECTS.find(p => p.aspect === payload.aspect) ?? DEMO_PROJECTS[0];
      const project = cloneDemoProject(template, payload.title);
      Object.assign(project, payload);
      const shortSide = payload.target_res === "4k" ? 2160 : payload.target_res === "1080p" ? 1080 : 720;
      const longSide = Math.round(shortSide * 16 / 9);
      project.timeline.format = { aspect: payload.aspect, fps: 30, target: payload.target_res,
        width: payload.aspect === "9:16" ? shortSide : longSide,
        height: payload.aspect === "9:16" ? longSide : shortSide };
      persistDemo();
      return project;
    });
  },

  async updateProject(id: string, patch: Partial<Pick<ProjectSummary, "title" | "target_res" | "qa_policy">>): Promise<ProjectSummary> {
    return (await requestJson(projectPath(id), mutation("PATCH", patch), () => {
      const project = Object.assign(demoProject(id), patch);
      persistDemo();
      return project;
    })).data;
  },

  async deleteProject(id: string): Promise<void> {
    await requestJson(projectPath(id), mutation("DELETE"), () => {
      demoProject(id);
      const state = demoWorkspace();
      state.projects = state.projects.filter(p => p.id !== id);
      delete state.history[id];
      persistDemo();
    });
  },

  async duplicateProject(id: string, title?: string): Promise<ProjectDetail> {
    return (await requestJson(`${projectPath(id)}/duplicate`, mutation("POST", { title }), () => {
      const source = demoProject(id);
      return cloneDemoProject(source, title || `${source.title} (copy)`);
    })).data;
  },

  async planProject(id: string, payload: { script_override?: string | null; brain_mode?: BrainMode } = {}): Promise<TimelineDocument> {
    return (await requestJson(`${projectPath(id)}/plan`, mutation("POST", payload), () => demoProject(id).timeline)).data;
  },

  async getChatGPTBrief(id: string): Promise<DirectorBriefExport> {
    return (await requestJson(`${projectPath(id)}/plan/brief`, {}, () => ({
      project_id: id,
      instructions: "Create an educational storyboard for " + demoProject(id).topic + ". Return JSON with a scenes array. Give each scene a kind, narration and duration_ms.",
      schema_hint: { required: ["palette", "look", "negative", "hook", "seo_title", "description", "hashtags", "scenes"] },
    }))).data;
  },

  async importChatGPTPlan(id: string, rawJson: string): Promise<TimelineDocument> {
    return (await requestJson(`${projectPath(id)}/plan/import`, mutation("POST", { raw_json: rawJson }), () => importDemoStoryboard(id, rawJson))).data;
  },

  async startGeneration(id: string): Promise<{ queued_jobs: number }> {
    return (await requestJson(`${projectPath(id)}/generate`, mutation("POST"), () => ({ queued_jobs: 0 }))).data;
  },

  async listQAReviews(id: string): Promise<QAReviewRecord[]> {
    return (await requestJson(`${projectPath(id)}/qa-reviews`, {}, () => DEMO_QA_REVIEWS.filter(r => demoProject(id).timeline.tracks.visual.some(s => s.id === r.scene_id)))).data;
  },

  async getQAAnalytics(id: string): Promise<QAAnalyticsResponse> {
    return (await requestJson<QAAnalyticsResponse>(`${projectPath(id)}/qa-analytics`, {}, () => {
      const models: QAAnalyticsResponse["models"] = {};
      for (const s of demoProject(id).timeline.tracks.visual) {
        if (!s.model || !s.qa || s.qa.score == null) continue;
        const m = models[s.model] ??= { reviews: 0, passed: 0, retry_count: 0, pass_rate: null };
        m.reviews++;
        if (["passed", "human_approved"].includes(s.qa.verdict)) m.passed++;
        m.retry_count += Math.max(0, s.qa.attempts - 1);
        m.pass_rate = m.passed / m.reviews;
      }
      return { project_id: id, models };
    })).data;
  },

  async approveScene(id: string, sceneId: string): Promise<void> {
    await requestJson(`${projectPath(id)}/scenes/${encodeURIComponent(sceneId)}/approve`, mutation("POST"),
      () => demoCommands(id, [{ type: "approve_scene_qa", scene_id: sceneId }]));
  },

  async regenerateScene(id: string, sceneId: string,
    body: { prompt?: string; model?: string; camera?: string; seed?: number }, expectedRevision?: number,
  ): Promise<{ ok: boolean; revision: number; queued_job_id: string }> {
    return (await requestJson(`${projectPath(id)}/scenes/${encodeURIComponent(sceneId)}/regenerate`, mutation("POST", body, expectedRevision), () => {
      const doc = demoCommands(id, [{ type: "regenerate_clip", scene_id: sceneId, ...body } as TimelineCommand], expectedRevision);
      return { ok: true, revision: doc.revision, queued_job_id: "" };
    })).data;
  },

  async revoiceScene(id: string, sceneId: string, expectedRevision?: number): Promise<{ ok: boolean; tts_job_id: string; align_job_id: string }> {
    return (await requestJson(`${projectPath(id)}/scenes/${encodeURIComponent(sceneId)}/revoice`, mutation("POST", undefined, expectedRevision),
      () => ({ ok: false, tts_job_id: "", align_job_id: "" }))).data;
  },

  async applyCommands(id: string, expectedRevision: number, commands: TimelineCommand[]): Promise<TimelineDocument> {
    const body = { expected_revision: expectedRevision, author: "user", commands };
    return (await requestJson(`${projectPath(id)}/timeline/commands`, mutation("POST", body, expectedRevision),
      () => demoCommands(id, commands, expectedRevision))).data;
  },

  async putTimeline(id: string, expectedRevision: number, document: TimelineDocument): Promise<TimelineDocument> {
    const body = { expected_revision: expectedRevision, author: "user", document };
    return (await requestJson(`${projectPath(id)}/timeline`, mutation("PUT", body, expectedRevision),
      () => saveDemoTimeline(id, document, expectedRevision))).data;
  },

  async listRevisions(id: string): Promise<RevisionSummary[]> {
    return (await requestJson(`${projectPath(id)}/revisions`, {}, () => demoHistory(id).map(({ document: _document, ...summary }) => summary))).data;
  },

  async restoreRevision(id: string, revision: number, expectedRevision?: number): Promise<TimelineDocument> {
    return (await requestJson(`${projectPath(id)}/revisions/${revision}/restore`, mutation("POST", undefined, expectedRevision), () => {
      const snapshot = demoHistory(id).find(row => row.revision === revision);
      if (!snapshot) throw new Error("Revision is unavailable.");
      return saveDemoTimeline(id, structuredClone(snapshot.document), expectedRevision);
    })).data;
  },

  async uploadMusicFile(id: string, file: File, options: MusicOptions = {}, expectedRevision?: number): Promise<MusicUploadResponse> {
    const form = new FormData();
    form.set("file", file);
    for (const [key, value] of Object.entries(options)) if (value !== undefined) form.set(key, String(value));
    return (await requestJson(`${projectPath(id)}/music`, { ...mutation("POST", undefined, expectedRevision), body: form },
      () => demoMusic(id, options.mood ?? "custom", options, expectedRevision))).data;
  },

  async applyMusicPreset(id: string, preset: MusicPreset, options: MusicOptions = {}, expectedRevision?: number): Promise<MusicUploadResponse> {
    return (await requestJson(`${projectPath(id)}/music`, mutation("POST", { ...options, preset }, expectedRevision),
      () => demoMusic(id, preset, options, expectedRevision))).data;
  },

  async removeMusic(id: string, expectedRevision?: number): Promise<TimelineDocument> {
    return (await requestJson(`${projectPath(id)}/music`, mutation("DELETE", undefined, expectedRevision), () => {
      const doc = structuredClone(demoProject(id).timeline);
      doc.tracks.music.asset = null;
      return saveDemoTimeline(id, doc, expectedRevision);
    })).data;
  },

  async copilotChat(id: string, message: string, selectedSceneId?: string | null): Promise<CopilotProposal> {
    return (await requestJson<CopilotProposal>(`${projectPath(id)}/copilot`, mutation("POST", { message, selected_scene_id: selectedSceneId ?? null }), () => {
      const scenes = demoProject(id).timeline.tracks.visual;
      const target = selectedSceneId || scenes[0]?.id;
      const review = message === "review_draft";
      const longest = Math.max(0, ...scenes.map(s => s.end_ms - s.start_ms)) / 1000;
      const critique = [
        `Pacing: The longest scene runs ${longest}s. Check that its narration fits.`,
        `Hook retention: Preview the first sentence and check how quickly it introduces the topic.`,
        `Diagram density: ${scenes.filter(s => s.kind === "diagram").length} of ${scenes.length} scenes use diagrams.`,
      ].join("\n");
      return {
        proposal_id: `prop_demo_${crypto.randomUUID()}`,
        assistant_message: review ? critique : "Preview a tighter pace and a new camera angle.",
        commands: review || !target ? [] : [{ type: "tighten_pauses", max_gap_ms: 80 }, { type: "regenerate_clip", scene_id: target, camera: "cinematic_orbit" }],
        affected_scenes: review || !target ? [] : [target], est_cu: review ? 0 : 0.08,
        requires_confirmation: false, status: "pending",
      };
    })).data;
  },

  async acceptCopilot(id: string, proposalId: string, expectedRevision: number, confirmGpuSpend: boolean): Promise<TimelineDocument> {
    return (await requestJson<TimelineDocument>(`${projectPath(id)}/copilot/${encodeURIComponent(proposalId)}/accept`, mutation("POST", {
      expected_revision: expectedRevision, confirm_gpu_spend: confirmGpuSpend,
    }, expectedRevision))).data;
  },

  listWorkers(): Promise<Result<WorkerRecord[]>> {
    return requestJson("/v1/workers", {}, () => demoWorkspace().workers);
  },

  async revokeWorker(id: string): Promise<void> {
    await requestJson(`/v1/workers/${encodeURIComponent(id)}`, mutation("DELETE"), () => {
      const state = demoWorkspace();
      if (!state.workers.some(worker => worker.id === id)) {
        throw new Error("Reconnect to FastAPI to revoke this worker.");
      }
      state.workers = state.workers.filter(w => w.id !== id);
      persistDemo();
    });
  },

  async createWorkerToken(name: string, profile: string): Promise<{ worker_id: string; worker_token: string }> {
    const local = profile === "media_local" || profile === "render_local";
    return (await requestJson<{ worker_id: string; worker_token: string }>("/v1/workers/tokens", mutation("POST",
      { name, profile: local ? profile.replace("_local", "") : profile, provider: local ? "local" : "google_colab" }))).data;
  },

  async getHealthMetrics(): Promise<HealthMetricsResponse> {
    return (await requestJson<HealthMetricsResponse>("/v1/health/metrics", {}, () => {
      const workers = { online: 0, busy: 0, offline: 0, total_cu_month: 0 };
      for (const w of demoWorkspace().workers) {
        workers[w.status]++;
        workers.total_cu_month += w.cu_used_month;
      }
      return {
        env: "demo", status: "offline", storage_provider: "local", redis_connected: false, workers,
        jobs: { queued: 0, leased: 0, running: 0, succeeded: 0, failed: 0, dead: 0 },
        vault: { synced: 0, missing: 0, mismatch: 0, pending: 0 },
        snapshots: { total_revisions: demoWorkspace().projects.reduce((sum, p) => sum + demoHistory(p.id).length, 0), backed_up_revisions: 0 },
      };
    })).data;
  },

  async listJobs(id: string): Promise<JobRecord[]> {
    return (await requestJson<JobRecord[]>(`${projectPath(id)}/jobs`, {}, () => [])).data;
  },

  listExports(id: string): Promise<Result<ExportRecord[]>> {
    return requestJson(`${projectPath(id)}/exports`, {}, () => DEMO_EXPORTS.filter(exp => exp.project_id === id));
  },

  async createExport(id: string, resolution: TargetResolution): Promise<ExportRecord> {
    return (await requestJson<ExportRecord>(`${projectPath(id)}/exports`, mutation("POST", { resolution }))).data;
  },

  async startGoogleDriveAuth(): Promise<string> {
    return (await requestJson<{ auth_url: string }>("/v1/auth/google/start")).data.auth_url;
  },

  async logout(): Promise<void> {
    await requestJson("/v1/auth/logout", mutation("POST"), () => {
      throw new ApiError("Reconnect to FastAPI to sign out of your owner session.", 0);
    });
    csrfToken = null;
    if (backendEnvironment === "phase1_vps") window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
  },
};

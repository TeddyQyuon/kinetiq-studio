import {
  applyCommandBatch,
  type ProjectDetail,
  type RevisionSummary,
  type TimelineCommand,
  type TimelineDocument,
  type VisualSceneItem,
} from "@kinetiq/contracts";
import { DEMO_PROJECTS, DEMO_WORKERS } from "./demo-data";

interface Snapshot extends RevisionSummary { document: TimelineDocument }
interface Workspace {
  projects: ProjectDetail[];
  history: Record<string, Snapshot[]>;
  workers: typeof DEMO_WORKERS;
}

const cacheKey = "kinetiq:demo-workspace:v2";
let workspace: Workspace | undefined;

export function demoWorkspace(): Workspace {
  if (!workspace) {
    if (typeof window !== "undefined") {
      try {
        const saved = window.sessionStorage.getItem(cacheKey);
        if (saved) workspace = JSON.parse(saved) as Workspace;
      } catch { /* Storage may be disabled. */ }
    }
    workspace ??= { projects: structuredClone(DEMO_PROJECTS), history: {}, workers: structuredClone(DEMO_WORKERS) };
  }
  return workspace;
}

export function persistDemo() {
  try { window.sessionStorage.setItem(cacheKey, JSON.stringify(workspace)); } catch { /* Keep edits in memory. */ }
}

export function demoProject(id: string) {
  const project = demoWorkspace().projects.find((p) => p.id === id);
  if (!project) throw new Error("This project is unavailable while the API is offline.");
  return project;
}

function snapshot(project: ProjectDetail, commands = 0): Snapshot {
  return {
    revision: project.timeline.revision, author: "user", commands_count: commands,
    drive_snapshot_id: null, created_at: project.updated_at, document: structuredClone(project.timeline),
  };
}

export function demoHistory(id: string): Snapshot[] {
  return demoWorkspace().history[id] ??= [snapshot(demoProject(id))];
}

export function saveDemoTimeline(id: string, document: TimelineDocument, expectedRevision?: number, commands = 0) {
  const project = demoProject(id);
  if (expectedRevision !== undefined && project.current_revision !== expectedRevision) {
    throw new Error("The demo timeline changed. Reload the latest revision to continue.");
  }
  const history = demoHistory(id);
  project.timeline = { ...document, revision: project.current_revision + 1 };
  project.current_revision = project.timeline.revision;
  project.updated_at = new Date().toISOString();
  project.scene_count = document.tracks.visual.length;
  project.ready_scenes = document.tracks.visual.filter(s => s.status === "ready").length;
  project.needs_review_count = document.tracks.visual.filter(s => s.status === "needs_review").length;
  history.unshift(snapshot(project, commands));
  history.splice(50);
  persistDemo();
  return structuredClone(project.timeline);
}

export function demoCommands(id: string, commands: TimelineCommand[], expected?: number) {
  return saveDemoTimeline(id, applyCommandBatch(demoProject(id).timeline, commands).doc, expected, commands.length);
}

export function cloneDemoProject(source: ProjectDetail, title: string) {
  const project = structuredClone(source);
  project.id = `prj_demo_${crypto.randomUUID()}`;
  project.title = title;
  project.status = "planned";
  project.drive_folder_id = null;
  project.created_at = project.updated_at = new Date().toISOString();
  project.timeline.project_id = project.id;
  project.timeline.revision = project.current_revision = 1;
  project.media_urls = {};
  project.timeline.tracks.visual.forEach((s, i) => {
    s.id = `${project.id}_scn_${String(i + 1).padStart(3, "0")}`;
    delete s.assets;
    if (s.kind !== "diagram") {
      s.qa = { score: null, attempts: 0, verdict: "pending" };
      s.status = "planned";
    }
  });
  project.timeline.tracks.voice = [];
  project.timeline.tracks.captions.words = [];
  project.timeline.tracks.captions.words_asset = null;
  project.timeline.tracks.music.asset = null;
  project.ready_scenes = project.timeline.tracks.visual.filter(s => s.status === "ready").length;
  project.needs_review_count = 0;
  demoWorkspace().projects.unshift(project);
  persistDemo();
  return project;
}

export function importDemoStoryboard(id: string, raw: string) {
  const board = JSON.parse(raw);
  if (!board || !Array.isArray(board.scenes) || !board.scenes.length) {
    throw new Error("The storyboard must contain a non-empty scenes array.");
  }
  const doc = structuredClone(demoProject(id).timeline);
  let cursor = 0;
  doc.tracks.visual = board.scenes.map((s: Record<string, unknown>, i: number): VisualSceneItem => {
    const { kind, narration } = s;
    if ((kind !== "ai_video" && kind !== "image_motion" && kind !== "diagram") || typeof narration !== "string") {
      throw new Error(`Check the kind and narration for scene ${i + 1}.`);
    }
    const start = cursor;
    const duration = Number(s.duration_ms ?? 6000);
    if (!Number.isFinite(duration) || duration <= 0) throw new Error(`Invalid duration in scene ${i + 1}.`);
    cursor += Math.round(duration);
    return {
      id: `${id}_scn_${String(i + 1).padStart(3, "0")}`, kind, narration, start_ms: start, end_ms: cursor,
      status: kind === "diagram" ? "ready" : "planned",
      template: s.diagram_template as VisualSceneItem["template"],
      props: s.diagram_props as VisualSceneItem["props"],
      model: s.model as VisualSceneItem["model"], camera: s.camera as VisualSceneItem["camera"],
      prompt: { ltx25: String(s.ltx25_prompt ?? ""), wan22: String(s.wan22_prompt ?? ""), qwen_image: String(s.qwen_image_prompt ?? "") },
    };
  });
  if (Array.isArray(board.palette) && board.palette.every((color: unknown) => typeof color === "string")) doc.style_bible.palette = board.palette;
  if (typeof board.look === "string") doc.style_bible.look = board.look;
  if (typeof board.negative === "string") doc.style_bible.negative = board.negative;
  doc.tracks.voice = [];
  doc.tracks.captions.words = [];
  return saveDemoTimeline(id, doc);
}

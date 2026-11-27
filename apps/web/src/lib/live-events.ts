import type {
  QAReviewRecord,
  SceneAssetRefs,
  SceneStatus,
  StudioEvent,
  TimelineDocument,
  VisualSceneItem,
} from "@kinetiq/contracts";

export interface SceneJobProgress {
  jobType: string;
  percent: number;
  status: "queued" | "running";
}

export const STUDIO_SSE_EVENT_TYPES = [
  "project.created",
  "pipeline.started",
  "project.ready",
  "timeline.updated",
  "job.queued",
  "job.progress",
  "job.completed",
  "job.failed",
  "job.lease_expired",
  "qa.reviewed",
  "asset.synced",
  "scene.updated",
  "copilot.proposal",
  "export.queued",
  "export.rendering",
  "export.failed",
  "job.claimed",
  "export.ready",
  "worker.online",
  "worker.offline",
] as const;

function jobTypeToSceneStatus(jobType: string, fallback: SceneStatus): SceneStatus {
  const lower = jobType.toLowerCase();
  if (lower.includes("qa")) return "qa_checking";
  if (lower.includes("keyframe")) return "generating_keyframe";
  if (lower.includes("i2v") || lower.includes("upscale")) return "generating_clip";
  if (lower.includes("audio") || lower.includes("tts") || lower.includes("kokoro")) {
    return "generating_audio";
  }
  return fallback;
}

function asVerdict(
  val: unknown,
  fallback: NonNullable<VisualSceneItem["qa"]>["verdict"],
): NonNullable<VisualSceneItem["qa"]>["verdict"] {
  if (
    val === "pending" ||
    val === "passed" ||
    val === "auto_healing" ||
    val === "needs_review" ||
    val === "human_approved"
  ) {
    return val;
  }
  return fallback;
}

export function applyStudioEventToTimeline(
  doc: TimelineDocument,
  event: StudioEvent,
): { doc: TimelineDocument; changed: boolean } {
  const payload = event.payload ?? {};
  const sceneId = typeof payload.scene_id === "string" ? payload.scene_id : null;

  if (!sceneId) {
    return { doc, changed: false };
  }

  const targetIdx = doc.tracks.visual.findIndex((s) => s.id === sceneId);
  if (targetIdx === -1) {
    return { doc, changed: false };
  }

  const prev = doc.tracks.visual[targetIdx];
  let nextScene: VisualSceneItem | null = null;

  switch (event.event_type) {
    case "job.queued": {
      const jobType = typeof payload.job_type === "string" ? payload.job_type : "";
      const nextStatus = jobTypeToSceneStatus(jobType, prev.status);
      if (nextStatus !== prev.status) {
        nextScene = { ...prev, status: nextStatus };
      }
      break;
    }

    case "job.progress": {
      const jobType = typeof payload.job_type === "string" ? payload.job_type : "";
      if (prev.status !== "ready") {
        const activeStatus = jobTypeToSceneStatus(jobType, prev.status);
        if (activeStatus !== prev.status) {
          nextScene = { ...prev, status: activeStatus };
        }
      }
      break;
    }

    case "job.failed":
    case "job.lease_expired": {
      const deadStatus =
        payload.status === "dead" || payload.new_status === "dead";
      if (deadStatus && prev.status !== "failed") {
        nextScene = { ...prev, status: "failed" };
      }
      break;
    }

    case "qa.reviewed": {
      const score = typeof payload.score === "number" ? payload.score : prev.qa?.score ?? null;
      const attempts =
        typeof payload.attempts === "number" ? payload.attempts : prev.qa?.attempts ?? 1;
      const verdict = asVerdict(payload.verdict, prev.qa?.verdict ?? "pending");
      const reason = typeof payload.reason === "string" ? payload.reason : prev.qa?.reason ?? null;
      const decision = typeof payload.decision === "string" ? payload.decision : "";

      let nextKind = prev.kind;
      let nextStatus: SceneStatus = prev.status;
      if (decision === "needs_review" || verdict === "needs_review") {
        nextStatus = "needs_review";
      } else if (decision === "auto_heal_retry") {
        nextStatus = "generating_keyframe";
      } else if (decision === "convert_diagram") {
        nextKind = "diagram";
        nextStatus = "ready";
      } else if (decision === "pass" || verdict === "passed") {
        nextStatus = prev.kind === "ai_video" ? "generating_clip" : "ready";
      }

      nextScene = {
        ...prev,
        kind: nextKind,
        status: nextStatus,
        qa: {
          score,
          attempts,
          verdict,
          reason,
        },
      };
      break;
    }

    case "asset.synced": {
      const kind = typeof payload.kind === "string" ? payload.kind : "";
      const assetId = typeof payload.asset_id === "string" ? payload.asset_id : null;
      if (!assetId) break;

      const assets: SceneAssetRefs = { ...(prev.assets ?? {}) };
      let nextStatus: SceneStatus = prev.status;

      if (kind === "keyframe") {
        assets.keyframe = assetId;
      } else if (kind === "master") {
        assets.master = assetId;
        nextStatus = "ready";
      } else if (kind === "proxy") {
        assets.proxy = assetId;
      } else if (kind === "upscaled_4k") {
        assets.upscaled_4k = assetId;
        nextStatus = "ready";
      }

      nextScene = {
        ...prev,
        assets,
        status: nextStatus,
      };
      break;
    }

    case "scene.updated": {
      const nextStatus =
        typeof payload.status === "string" ? (payload.status as SceneStatus) : prev.status;
      if (nextStatus !== prev.status) {
        nextScene = { ...prev, status: nextStatus };
      }
      break;
    }

    default:
      break;
  }

  if (!nextScene) {
    return { doc, changed: false };
  }

  const nextVisual = doc.tracks.visual.slice();
  nextVisual[targetIdx] = nextScene;
  return {
    doc: {
      ...doc,
      tracks: {
        ...doc.tracks,
        visual: nextVisual,
      },
    },
    changed: true,
  };
}

export function applyStudioEventToProgress(
  current: Record<string, SceneJobProgress>,
  event: StudioEvent,
): Record<string, SceneJobProgress> {
  const payload = event.payload ?? {};

  if (event.event_type === "project.ready") {
    return Object.keys(current).length === 0 ? current : {};
  }

  const sceneId = typeof payload.scene_id === "string" ? payload.scene_id : null;
  if (!sceneId) return current;

  if (event.event_type === "job.queued") {
    const jobType = typeof payload.job_type === "string" ? payload.job_type : "job";
    return {
      ...current,
      [sceneId]: {
        jobType,
        percent: 0,
        status: "queued",
      },
    };
  }

  if (event.event_type === "job.progress") {
    const jobType =
      typeof payload.job_type === "string"
        ? payload.job_type
        : current[sceneId]?.jobType ?? "job";
    const rawPct = typeof payload.progress_pct === "number" ? payload.progress_pct : 0;
    const percent = Math.max(0, Math.min(100, Math.round(rawPct)));
    return {
      ...current,
      [sceneId]: {
        jobType,
        percent,
        status: "running",
      },
    };
  }

  if (
    event.event_type === "job.completed" ||
    event.event_type === "job.failed" ||
    event.event_type === "job.lease_expired" ||
    (event.event_type === "asset.synced" &&
      (payload.kind === "master" || payload.kind === "proxy" || payload.kind === "upscaled_4k"))
  ) {
    if (!(sceneId in current)) return current;
    const next = { ...current };
    delete next[sceneId];
    return next;
  }

  return current;
}

export function applyStudioEventToQAReviews(
  reviews: QAReviewRecord[],
  event: StudioEvent,
): QAReviewRecord[] {
  if (event.event_type !== "qa.reviewed") return reviews;
  const payload = event.payload ?? {};
  const sceneId = typeof payload.scene_id === "string" ? payload.scene_id : null;
  if (!sceneId) return reviews;

  const existing = reviews.find((r) => r.scene_id === sceneId);
  const score = typeof payload.score === "number" ? payload.score : existing?.score ?? 0;
  const verdict = typeof payload.verdict === "string" ? payload.verdict : existing?.verdict ?? "pending";
  const reason = typeof payload.reason === "string" ? payload.reason : existing?.reason ?? "";

  const updated: QAReviewRecord = {
    id: existing?.id ?? `qar_live_${sceneId}_${event.id}`,
    scene_id: sceneId,
    asset_id: existing?.asset_id ?? null,
    narration_match: existing?.narration_match ?? score,
    artifact_free: existing?.artifact_free ?? score,
    style_consistency: existing?.style_consistency ?? score,
    safe_zone: existing?.safe_zone ?? score,
    score,
    verdict,
    reason,
    fix_action: existing?.fix_action ?? null,
    created_at: event.created_at || new Date().toISOString(),
  };

  const without = reviews.filter((r) => r.scene_id !== sceneId);
  return [updated, ...without];
}
"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  CheckCircle,
  Film,
  Play,
  Radio,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  Wand2,
} from "lucide-react";
import type { ProjectDetail, QAAnalyticsResponse, QAReviewRecord, StudioEvent, WorkerRecord } from "@kinetiq/contracts";
import { api } from "@/lib/api-client";
import {
  applyStudioEventToProgress,
  applyStudioEventToQAReviews,
  applyStudioEventToTimeline,
  type SceneJobProgress,
} from "@/lib/live-events";
import { useProjectEvents } from "@/lib/use-project-events";
import { hasLowVram, isOnlineGpuWorker } from "@/lib/worker-capabilities";
import { StatusBadge } from "@/components/StatusBadge";

export default function PlanAndQAPage() {
  const { id } = useParams<{ id: string }>();
  return <PlanBoard key={id} projectId={id} />;
}

function PlanBoard({ projectId }: { projectId: string }) {
  const [project, setProject] = useState<ProjectDetail | null>(null);
  // SSE overlays can change displayed status and QA before a timeline revision is saved.
  const [savedTimeline, setSavedTimeline] = useState<ProjectDetail["timeline"] | null>(null);
  const [gpuWorkers, setGpuWorkers] = useState<WorkerRecord[]>([]);
  const [reviews, setReviews] = useState<QAReviewRecord[]>([]);
  const [sceneProgress, setSceneProgress] = useState<Record<string, SceneJobProgress>>({});
  const [busyMsg, setBusyMsg] = useState<string | null>(null);
  const [isDemo, setIsDemo] = useState(false);

  const [analytics, setAnalytics] = useState<QAAnalyticsResponse | null>(null);
  const [brief, setBrief] = useState("");
  const [rawPlan, setRawPlan] = useState("");
  const [pending, setPending] = useState(false);
  const latestLoad = useRef(0);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    const request = ++latestLoad.current;
    try {
      const [p, r, stats, fleet] = await Promise.all([
        api.getProject(projectId), api.listQAReviews(projectId), api.getQAAnalytics(projectId).catch(() => null),
        api.listWorkers().catch(() => null),
      ]);
      if (request !== latestLoad.current) return;
      setProject(p.data);
      setSavedTimeline(p.data.timeline);
      setGpuWorkers(fleet && fleet.demo === p.demo ? fleet.data.filter(isOnlineGpuWorker) : []);
      setIsDemo(p.demo);
      setReviews(r);
      setAnalytics(stats);
    } catch (error) {
      if (request === latestLoad.current) setBusyMsg(error instanceof Error ? error.message : "Could not load storyboard.");
    }
  }, [projectId]);

  useEffect(() => {
    const notice = new URLSearchParams(window.location.search).get("notice");
    setBusyMsg(notice === "plan_failed" ? "Project created with its initial storyboard. The AI director was unavailable. Use Re-Plan to retry."
      : notice === "import_failed" ? "Project created. The storyboard import failed. Check the JSON and import it below." : null);
    void load();
    return () => {
      ++latestLoad.current;
      if (refreshTimer.current !== null) clearTimeout(refreshTimer.current);
    };
  }, [load]);

  async function run(action: () => Promise<void>) {
    setPending(true);
    setBusyMsg(null);
    try {
      await action();
    } catch (error) {
      setBusyMsg(error instanceof Error ? error.message : "The action failed.");
    } finally {
      setPending(false);
    }
  }

  const handleLiveEvent = useCallback(
    (event: StudioEvent) => {
      setSceneProgress((prev) => applyStudioEventToProgress(prev, event));
      setReviews((prev) => applyStudioEventToQAReviews(prev, event));
      setProject((prev) => {
        if (!prev) return prev;
        const timelineResult = applyStudioEventToTimeline(prev.timeline, event);
        const nextTimeline = timelineResult.doc;
        const nextReady = nextTimeline.tracks.visual.filter((s) => s.status === "ready").length;
        const nextNeedsReview = nextTimeline.tracks.visual.filter(
          (s) => s.status === "needs_review",
        ).length;
        const nextStatus = event.event_type === "project.ready" ? "ready" : prev.status;

        if (!timelineResult.changed && nextStatus === prev.status) {
          return prev;
        }
        return {
          ...prev,
          status: nextStatus,
          ready_scenes: nextReady,
          needs_review_count: nextNeedsReview,
          timeline: nextTimeline,
        };
      });

      // Replayed events arrive in bursts. Refresh signed URLs and QA totals once per burst.
      const refreshNeeded = ["timeline.updated", "project.ready", "qa.reviewed", "worker.online", "worker.offline"].includes(event.event_type)
        || (event.event_type === "asset.synced" && event.payload.kind === "keyframe");
      if (refreshNeeded && refreshTimer.current === null) {
        refreshTimer.current = setTimeout(() => {
          refreshTimer.current = null;
          void load();
        }, 150);
      }
    },
    [load],
  );

  const {
    status: sseStatus,
    lastEventId,
    reconnectNow,
  } = useProjectEvents({
    projectId,
    enabled: !isDemo && Boolean(project),
    onEvent: handleLiveEvent,
  });

  async function handleStartGenerate() {
    if (!project) return;
    if (isDemo) {
      setBusyMsg("Start FastAPI and a Colab worker to generate scenes.");
      return;
    }
    setBusyMsg("Dispatching scene generation jobs…");
    const res = await api.startGeneration(project.id);
    setBusyMsg(`Queued ${res.queued_jobs} scene generation jobs.`);
    await load();
  }

  async function handleApproveScene(sceneId: string) {
    if (!project) return;
    await api.approveScene(project.id, sceneId);
    await load();
  }

  async function handleConvertToDiagram(sceneId: string) {
    if (!project) return;
    const target = project.timeline.tracks.visual.find((s) => s.id === sceneId);
    if (!target) return;
    await api.applyCommands(project.id, project.timeline.revision, [
      {
        type: "convert_scene_to_diagram",
        scene_id: sceneId,
        template: "StepBreakdown",
        props: { title: "Visual Breakdown", steps: [target.narration] },
      },
    ]);
    await load();
  }

  if (!project || !savedTimeline) {
    return <div className="p-8 text-sm text-zinc-400" role="status">{busyMsg || "Loading project plan & QA board…"}</div>;
  }

  // The scheduler routes every AI video without an explicit Wan model to LTX.
  const ltxScenes = savedTimeline.tracks.visual
    .map((scene, index) => ({ scene, index }))
    .filter(({ scene }) => scene.kind === "ai_video" && scene.model !== "wan22_i2v");
  const hasWanWorker = gpuWorkers.some(worker => worker.capabilities.includes("i2v_wan22"));
  const showProfileMismatch = ltxScenes.length > 0
    && !gpuWorkers.some(worker => worker.capabilities.includes("i2v_ltx25"))
    && (hasWanWorker || gpuWorkers.some(hasLowVram));

  const sseBadgeClass =
    sseStatus === "connected"
      ? "border-emerald-500/30 bg-emerald-500/15 text-emerald-300"
      : sseStatus === "connecting" || sseStatus === "reconnecting"
        ? "border-amber-500/30 bg-amber-500/15 text-amber-300"
        : "border-zinc-700 bg-zinc-900 text-zinc-400";

  const sseLabel =
    sseStatus === "connected"
      ? `SSE Live${lastEventId > 0 ? ` · #${lastEventId}` : ""}`
      : sseStatus === "reconnecting"
        ? "SSE Reconnecting..."
        : sseStatus === "connecting"
          ? "SSE Connecting..."
          : sseStatus === "demo"
            ? "Demo Mode (SSE Idle)"
            : "SSE Offline";

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6 md:p-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge value={project.status} />
            <span
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${sseBadgeClass}`}
            >
              <Radio className="h-3 w-3" />
              {sseLabel}
            </span>
            {(sseStatus === "reconnecting" || sseStatus === "closed") && !isDemo && (
              <button
                type="button"
                onClick={reconnectNow}
                className="rounded-full border border-zinc-700 bg-zinc-900 px-2 py-0.5 text-[11px] text-zinc-300 hover:bg-zinc-800"
              >
                Reconnect
              </button>
            )}
            <span className="text-xs text-zinc-400">
              Policy: <strong className="text-zinc-200">{project.qa_policy}</strong>
            </span>
          </div>
          <h1 className="mt-2 text-2xl font-bold">{project.title} — Plan &amp; HOTL QA Board</h1>
        </div>
        <div className="flex flex-wrap gap-2.5">
          <button disabled={pending} className="rounded-lg border border-amber-500/40 px-4 py-2 text-sm text-amber-200 disabled:opacity-50"
            onClick={() => run(async () => {
              await api.planProject(project.id, { brain_mode: project.brain_mode });
              setBusyMsg(isDemo ? "Demo storyboard loaded." : "Storyboard updated.");
              await load();
            })}>
            Re-Plan with AI Director
          </button>
          <button
            disabled={pending}
            onClick={() => run(handleStartGenerate)}
            className="inline-flex items-center gap-2 rounded-lg bg-amber-500 px-4 py-2 text-sm font-semibold text-zinc-950 hover:bg-amber-400"
          >
            <Play className="h-4 w-4" />
            Generate All Scenes
          </button>
          <Link
            href={`/projects/${project.id}/editor`}
            className="inline-flex items-center gap-2 rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2 text-sm font-medium hover:bg-zinc-800"
          >
            <Film className="h-4 w-4 text-cyan-400" />
            Open AI Editor
          </Link>
        </div>
      </div>

      {busyMsg && (
        <div role="status" className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-2.5 text-xs text-amber-200">
          {busyMsg}
        </div>
      )}

      {showProfileMismatch && (
        <aside className="space-y-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
          <p role="status">
            {ltxScenes.length} AI {ltxScenes.length === 1 ? "scene needs" : "scenes need"} LTX-2.5. No online GPU worker provides it.{" "}
            {hasWanWorker ? "Wan 2.2 Fast is available on an online worker." : "Start the wan22_fast profile on the lower-VRAM worker to generate with Wan 2.2 Fast."}{" "}
            Switching selects Wan for future clip jobs.
          </p>
          <button type="button" disabled={pending}
            className="rounded-md border border-amber-500/50 px-3 py-1.5 font-semibold hover:bg-amber-500/15 disabled:opacity-50"
            onClick={() => run(async () => {
              try {
                await api.applyCommands(project.id, savedTimeline.revision, ltxScenes.map(({ scene, index }) => ({
                  type: "restore_scene", scene: { ...scene, model: "wan22_i2v" }, index,
                })));
                setBusyMsg("AI scenes switched to Wan 2.2 Fast.");
              } finally {
                await load();
              }
            })}>
            Switch AI scenes to Wan 2.2 Fast
          </button>
        </aside>
      )}

      <div className="rounded-xl border border-zinc-800 bg-[#111216] p-5">
        <div className="flex items-center gap-2 text-sm font-semibold text-amber-300">
          <Sparkles className="h-4 w-4" />
          Global Visual Style Bible (Locked Across All Scenes)
        </div>
        <p className="mt-2 text-xs text-zinc-300">{project.timeline.style_bible.look}</p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {project.timeline.style_bible.palette.map((hex) => (
            <span
              key={hex}
              className="inline-flex items-center gap-1.5 rounded-full border border-zinc-700 bg-zinc-900 px-2.5 py-1 text-[11px]"
            >
              <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: hex }} />
              {hex}
            </span>
          ))}
          <span className="text-[11px] text-zinc-400">
            Safe zone: {project.timeline.style_bible.safe_zone}
          </span>
        </div>
      </div>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold">Model QA Analytics</h2>
        {analytics ? <div className="grid gap-3 sm:grid-cols-3">
          {(["ltx25_fp8", "wan22_i2v", "qwen_image"] as const).map((model) => {
            const stats = analytics.models[model];
            return <div key={model} className="rounded-lg border border-zinc-800 bg-[#111216] p-3">
              <h3 className="font-mono text-xs text-zinc-300">{model}</h3>
              <div className="mt-1 text-sm font-semibold">{stats?.pass_rate == null ? "No rated renders" : `${Math.round(stats.pass_rate * 100)}% pass rate`}</div>
              <div className="text-xs text-zinc-400">{stats?.reviews ?? 0} reviews · {stats?.retry_count ?? 0} retries</div>
            </div>;
          })}
        </div> : <p className="text-xs text-zinc-400">QA analytics unavailable.</p>}
      </section>

      <details className="rounded-xl border border-zinc-800 bg-[#111216] p-4" onToggle={(e) => {
        if (e.currentTarget.open && !brief) {
          api.getChatGPTBrief(projectId).then((result) => {
            setBrief(`${result.instructions}\n\n${JSON.stringify(result.schema_hint, null, 2)}`);
          }).catch((error: Error) => setBusyMsg(error.message));
        }
      }}>
        <summary className="cursor-pointer text-sm font-semibold">Import ChatGPT JSON</summary>
        <div className="mt-4 grid gap-3">
          {brief && <label className="text-xs text-zinc-400">Director brief
            <textarea readOnly rows={4} className="mt-1 block w-full rounded bg-zinc-900 p-2 font-mono text-zinc-200" value={brief} />
          </label>}
          <label className="text-xs text-zinc-400">Storyboard JSON
            <textarea value={rawPlan} onChange={(e) => setRawPlan(e.target.value)} rows={5}
              className="mt-1 block w-full rounded border border-zinc-700 bg-zinc-950 p-3 font-mono text-zinc-200" />
          </label>
          <button disabled={pending || !rawPlan.trim()} className="justify-self-start rounded bg-amber-500 px-3 py-2 text-xs font-semibold text-zinc-950 disabled:opacity-50"
            onClick={() => run(async () => {
              await api.importChatGPTPlan(project.id, rawPlan);
              setRawPlan("");
              setBusyMsg("Storyboard imported.");
              await load();
            })}>Import storyboard</button>
        </div>
      </details>

      <div className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-zinc-400">
          Scene Storyboard &amp; Automatic Gemini Vision QA ({project.timeline.tracks.visual.length}{" "}
          Scenes)
        </h2>

        <div className="grid gap-3">
          {project.timeline.tracks.visual.map((scene, idx) => {
            const review = reviews.find((r) => r.scene_id === scene.id);
            const thumbnail = scene.assets?.keyframe ? project.media_urls[scene.assets.keyframe] : null;
            const liveProg = sceneProgress[scene.id];
            const needsHuman = scene.status === "needs_review";
            return (
              <div
                key={scene.id}
                className={`rounded-xl border p-4 transition ${
                  needsHuman
                    ? "border-rose-500/40 bg-rose-500/5"
                    : "border-zinc-800 bg-[#111216]"
                }`}
              >
                <div className="flex flex-wrap items-start justify-between gap-4">
                  {thumbnail && !thumbnail.startsWith("mock://") && (
                    <img src={thumbnail} alt={`Keyframe for scene ${idx + 1}`} width={128} height={72}
                      loading="lazy" className="max-h-24 w-32 rounded-lg bg-zinc-900 object-contain" />
                  )}
                  <div className="space-y-1.5 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-xs text-zinc-400">
                        #{String(idx + 1).padStart(2, "0")}
                      </span>
                      <span className="rounded bg-zinc-800 px-2 py-0.5 text-[11px] font-semibold uppercase">
                        {scene.kind}
                      </span>
                      <StatusBadge value={scene.status} />
                      {scene.qa?.score !== null && scene.qa?.score !== undefined && (
                        <span className="rounded-full bg-zinc-900 border border-zinc-700 px-2.5 py-0.5 text-[11px] font-semibold text-cyan-300">
                          QA Score: {scene.qa.score.toFixed(1)} / 10
                          {scene.qa.verdict ? ` · ${scene.qa.verdict.replace(/_/g, " ")}` : ""}
                        </span>
                      )}
                    </div>
                    <p className="text-sm font-medium text-zinc-100">{scene.narration}</p>
                    {scene.prompt?.ltx25 && (
                      <p className="text-xs text-zinc-400">Prompt: {scene.prompt.ltx25}</p>
                    )}
                    {(review?.reason || scene.qa?.reason) && (
                      <p className="text-xs text-amber-200/90">
                        <ShieldAlert className="mr-1 inline h-3.5 w-3.5" />
                        {review?.reason ?? scene.qa?.reason}
                      </p>
                    )}

                    {liveProg && (
                      <div className="mt-2 max-w-md space-y-1">
                        <div className="flex items-center justify-between text-[11px] text-amber-300">
                          <span>
                            {liveProg.jobType} ({liveProg.status})
                          </span>
                          <span>{liveProg.percent}%</span>
                        </div>
                        <div className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-800">
                          <div
                            className="h-full rounded-full bg-amber-400 transition-all duration-300"
                            style={{ width: `${Math.max(4, liveProg.percent)}%` }}
                          />
                        </div>
                      </div>
                    )}
                  </div>

                  <div className="flex flex-wrap gap-2">
                    {needsHuman && (
                      <button disabled={pending}
                          onClick={() => run(() => handleApproveScene(scene.id))}
                          className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-zinc-950 hover:bg-emerald-400"
                        >
                          <CheckCircle className="h-3.5 w-3.5" />
                          Approve &amp; Render Clip
                        </button>
                    )}
                    {scene.kind !== "diagram" && (
                        <button disabled={pending}
                          onClick={() => run(() => handleConvertToDiagram(scene.id))}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-cyan-500/40 bg-cyan-500/10 px-3 py-1.5 text-xs font-medium text-cyan-200 hover:bg-cyan-500/20"
                        >
                          <Wand2 className="h-3.5 w-3.5" />
                          Convert to Diagram
                        </button>
                    )}
                    {!isDemo && scene.kind !== "diagram" && (
                      <button
                        disabled={pending}
                        onClick={() => run(async () => { await api.regenerateScene(project.id, scene.id, {}, project.timeline.revision); await load(); })}
                        className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800"
                      >
                        <RefreshCw className="h-3.5 w-3.5" />
                        Re-roll
                      </button>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

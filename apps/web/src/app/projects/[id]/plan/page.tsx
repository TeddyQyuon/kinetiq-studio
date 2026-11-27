"use client";

import React, { useCallback, useEffect, useState } from "react";
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
import type { ProjectDetail, QAReviewRecord, StudioEvent } from "@kinetiq/contracts";
import { api } from "@/lib/api-client";
import {
  applyStudioEventToProgress,
  applyStudioEventToQAReviews,
  applyStudioEventToTimeline,
  type SceneJobProgress,
} from "@/lib/live-events";
import { useProjectEvents } from "@/lib/use-project-events";
import { StatusBadge } from "@/components/StatusBadge";

export default function PlanAndQAPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [reviews, setReviews] = useState<QAReviewRecord[]>([]);
  const [sceneProgress, setSceneProgress] = useState<Record<string, SceneJobProgress>>({});
  const [busyMsg, setBusyMsg] = useState<string | null>(null);
  const [isDemo, setIsDemo] = useState(false);

  const load = useCallback(async () => {
    const [p, r] = await Promise.all([api.getProject(projectId), api.listQAReviews(projectId)]);
    setProject(p.data);
    setIsDemo(p.demo);
    setReviews(r);
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

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

      if (event.event_type === "timeline.updated" || event.event_type === "project.ready") {
        void load();
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
    setBusyMsg("Dispatching keyframes to Colab GPU + Auto Vision QA...");
    try {
      if (!isDemo) {
        const res = await api.startGeneration(project.id);
        setBusyMsg(`Queued ${res.queued_jobs} keyframe job(s) for Colab.`);
        await load();
      } else {
        setBusyMsg("Demo Mode: Start FastAPI + Colab worker to run live generation.");
      }
    } catch (e) {
      setBusyMsg(e instanceof Error ? e.message : "Dispatch failed");
    }
  }

  async function handleApproveScene(sceneId: string) {
    if (!project) return;
    if (isDemo) {
      setProject({
        ...project,
        timeline: {
          ...project.timeline,
          tracks: {
            ...project.timeline.tracks,
            visual: project.timeline.tracks.visual.map((s) =>
              s.id === sceneId
                ? {
                    ...s,
                    status: "ready",
                    qa: { score: 8.5, attempts: 1, verdict: "human_approved" },
                  }
                : s,
            ),
          },
        },
      });
      return;
    }
    await api.approveScene(project.id, sceneId);
    await load();
  }

  async function handleConvertToDiagram(sceneId: string) {
    if (!project) return;
    const target = project.timeline.tracks.visual.find((s) => s.id === sceneId);
    if (!target) return;
    if (isDemo) {
      setProject({
        ...project,
        timeline: {
          ...project.timeline,
          tracks: {
            ...project.timeline.tracks,
            visual: project.timeline.tracks.visual.map((s) =>
              s.id === sceneId
                ? {
                    ...s,
                    kind: "diagram",
                    template: "StepBreakdown",
                    props: { title: "Visual Breakdown", steps: [target.narration] },
                    status: "ready",
                  }
                : s,
            ),
          },
        },
      });
      return;
    }
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

  if (!project) {
    return <div className="p-8 text-sm text-zinc-400">Loading project plan &amp; QA board...</div>;
  }

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
          <button
            onClick={handleStartGenerate}
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
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-2.5 text-xs text-amber-200">
          {busyMsg}
        </div>
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

      <div className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-zinc-400">
          Scene Storyboard &amp; Automatic Gemini Vision QA ({project.timeline.tracks.visual.length}{" "}
          Scenes)
        </h2>

        <div className="grid gap-3">
          {project.timeline.tracks.visual.map((scene, idx) => {
            const review = reviews.find((r) => r.scene_id === scene.id);
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
                      <>
                        <button
                          onClick={() => handleApproveScene(scene.id)}
                          className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-zinc-950 hover:bg-emerald-400"
                        >
                          <CheckCircle className="h-3.5 w-3.5" />
                          Approve &amp; Render Clip
                        </button>
                        <button
                          onClick={() => handleConvertToDiagram(scene.id)}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-cyan-500/40 bg-cyan-500/10 px-3 py-1.5 text-xs font-medium text-cyan-200 hover:bg-cyan-500/20"
                        >
                          <Wand2 className="h-3.5 w-3.5" />
                          Convert to Diagram
                        </button>
                      </>
                    )}
                    {!isDemo && scene.kind !== "diagram" && (
                      <button
                        onClick={() => api.regenerateScene(project.id, scene.id, {}).then(load)}
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

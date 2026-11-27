"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Check, ClipboardCopy, Download, Film, HardDrive, Sparkles } from "lucide-react";
import type { ExportRecord, ProjectDetail, TargetResolution } from "@kinetiq/contracts";
import { api } from "@/lib/api-client";
import { useProjectEvents } from "@/lib/use-project-events";
import { StatusBadge } from "@/components/StatusBadge";

export default function ExportsPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [exportsList, setExportsList] = useState<ExportRecord[]>([]);
  const [isDemo, setIsDemo] = useState(false);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  const [activeRenders, setActiveRenders] = useState(0);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latestLoad = useRef(0);

  const load = useCallback(async (includeProject = false) => {
    const request = ++latestLoad.current;
    try {
      const [p, ex] = await Promise.all([
        includeProject ? api.getProject(projectId) : null,
        api.listExports(projectId),
      ]);
      if (request !== latestLoad.current) return;
      if (p) setProject(p.data);
      setExportsList(ex.data);
      setIsDemo(Boolean(p?.demo || ex.demo));

      // Older control planes leave the export queued while its render job is running.
      if (!ex.demo && ex.data.some((record) => record.status === "queued")) {
        const jobs = await api.listJobs(projectId);
        if (request !== latestLoad.current) return;
        setActiveRenders(jobs.filter((job) => job.job_type === "render" &&
          (job.status === "leased" || job.status === "running")).length);
      } else {
        setActiveRenders(0);
      }
    } catch (error) {
      if (request === latestLoad.current) setMsg(error instanceof Error ? error.message : "Could not load exports.");
    }
  }, [projectId]);

  useEffect(() => {
    setProject(null);
    setMsg(null);
    setActiveRenders(0);
    setPreview(null);
    void load(true);
    return () => {
      ++latestLoad.current;
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
      refreshTimer.current = null;
    };
  }, [load]);

  useProjectEvents({
    projectId,
    enabled: project?.id === projectId && !isDemo,
    onEvent: (event) => {
      const relevant = event.event_type.startsWith("export.") || event.payload.job_type === "render" ||
        event.event_type === "job.failed" || event.event_type === "job.lease_expired";
      if (!relevant || refreshTimer.current) return;
      // Replay can deliver a whole render's history together.
      refreshTimer.current = setTimeout(() => {
        refreshTimer.current = null;
        void load();
      }, 120);
    },
  });

  async function handleStartExport(res: TargetResolution) {
    if (isDemo) {
      setMsg("Start FastAPI and a render worker to export this project.");
      return;
    }
    setSubmitting(true);
    try {
      await api.createExport(projectId, res);
      setMsg(`Queued the ${res.toUpperCase()} render. The master will appear below when ready.`);
      await load();
    } catch (error) {
      setMsg(error instanceof Error ? error.message : "Could not start export.");
    } finally {
      setSubmitting(false);
    }
  }

  async function downloadSubtitles(exp: ExportRecord) {
    if (!exp.srt_url) return;
    try {
      // Vault subtitle links expire. Ask for a fresh signed URL at download time.
      const records = await api.listExports(projectId);
      const srtUrl = records.data.find((record) => record.id === exp.id)?.srt_url;
      if (!srtUrl) throw new Error("Subtitles are currently unavailable.");
      const response = await fetch(srtUrl, { credentials: "include" });
      if (!response.ok) throw new Error(`Subtitle download failed (HTTP ${response.status}).`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = `${exp.id}.srt`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      setMsg(error instanceof Error ? error.message : "Could not download subtitles.");
    }
  }

  async function copyText(key: string, value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopiedKey(key);
      setTimeout(() => setCopiedKey(null), 1500);
    } catch {
      setMsg("Copy is unavailable. Select the publish-kit text to copy it manually.");
    }
  }

  if (!project) {
    return <div className="p-8 text-sm text-zinc-400" role="status">{msg || "Loading export center…"}</div>;
  }

  const pendingExports = exportsList.filter((exp) => exp.status === "queued" || exp.status === "rendering");
  const singleRunning = pendingExports.length === 1 && activeRenders === 1 ? pendingExports[0].id : null;
  const latestKit = exportsList[0]?.publish_kit ?? {
    hook: project.timeline.tracks.visual[0]?.narration ?? project.topic,
    seo_title: `${project.title} (${project.target_res.toUpperCase()})`,
    description: project.topic,
    hashtags: ["#education", "#explainer", "#knowledge", "#learnontiktok"],
    ai_disclosure_note:
      "Turn ON the TikTok 'AI-generated content' and Facebook AI info toggle when uploading manually.",
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Export &amp; Laptop Download Center</h1>
          <p className="mt-1 text-sm text-zinc-400">
            {project.title} · {project.aspect}
          </p>
        </div>
        <Link
          href={`/projects/${project.id}/editor`}
          className="inline-flex items-center gap-2 rounded-lg border border-zinc-700 bg-zinc-900 px-3.5 py-2 text-xs font-medium hover:bg-zinc-800"
        >
          <Film className="h-4 w-4 text-amber-400" />
          Back to AI Editor
        </Link>
      </div>

      {msg && (
        <div role="status" className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-2.5 text-xs text-amber-200">
          {msg}
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-xl border border-zinc-800 bg-[#111216] p-5 space-y-3">
          <div className="text-sm font-bold">Render Final Master</div>
          <p className="text-xs text-zinc-400">
            Uses full-quality Drive master clips + native Remotion vector diagrams + H.264 High (`crf 17`) &amp; `-14 LUFS` audio.
          </p>
          <div className="flex flex-wrap gap-2.5 pt-1">
            <button
              disabled={submitting}
              onClick={() => handleStartExport("1080p")}
              className="rounded-lg bg-amber-500 px-4 py-2 text-xs font-semibold text-zinc-950 hover:bg-amber-400"
            >
              Render Native 1080p MP4
            </button>
            <button
              disabled={submitting}
              onClick={() => handleStartExport("4k")}
              className="rounded-lg border border-cyan-500/40 bg-cyan-500/15 px-4 py-2 text-xs font-semibold text-cyan-200 hover:bg-cyan-500/25"
            >
              Render 4K Master (FlashVSR+)
            </button>
          </div>
        </div>

        <div className="rounded-xl border border-zinc-800 bg-[#111216] p-5 space-y-2 text-xs">
          <div className="flex items-center gap-2 text-sm font-bold">
            <HardDrive className="h-4 w-4 text-emerald-400" />
            TikTok &amp; Facebook Compliance Specs
          </div>
          <div className="text-zinc-300">• Video: H.264 High Profile, `yuv420p`, `+faststart`</div>
          <div className="text-zinc-300">• Audio: AAC 192 kbps 48 kHz normalized to -14 LUFS</div>
          <div className="text-amber-300">• {latestKit.ai_disclosure_note}</div>
        </div>
      </div>

      <div className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-zinc-400">Saved Masters</h2>
        {activeRenders > 0 && pendingExports.length > 1 && (
          <p role="status" className="text-xs text-amber-300">Rendering {activeRenders} {activeRenders === 1 ? "master" : "masters"}</p>
        )}
        {exportsList.length === 0 && (
          <p className="text-sm text-zinc-400">Your first export will appear here when queued.</p>
        )}
        {exportsList.map((exp) => (
          <div
            key={exp.id}
            className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-zinc-800 bg-[#111216] p-4"
          >
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs text-zinc-300">{exp.id}</span>
                <span className="rounded bg-zinc-800 px-2 py-0.5 text-[11px] uppercase text-cyan-300">
                  {exp.resolution}
                </span>
                <StatusBadge value={exp.id === singleRunning ? "rendering" : exp.status} />
              </div>
              <div className="text-xs text-zinc-400">
                Revision {exp.revision}{exp.drive_file_id && ` · Drive ID: ${exp.drive_file_id}`}
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              {exp.srt_url && (
                <button className="rounded-lg border border-zinc-700 px-3 py-2 text-xs" onClick={() => downloadSubtitles(exp)}>
                  Download Subtitles (.srt)
                </button>
              )}
              {exp.status === "ready" && exp.download_url && (
                <button aria-expanded={preview === exp.id} className="rounded-lg border border-zinc-700 px-3 py-2 text-xs"
                  onClick={() => setPreview(preview === exp.id ? null : exp.id)}>
                  Preview Rendered Master
                </button>
              )}
              {exp.download_url ? (
                <a
                  href={exp.download_url}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-500 px-3.5 py-2 text-xs font-semibold text-zinc-950 hover:bg-emerald-400"
                >
                  <Download className="h-3.5 w-3.5" />
                  Download MP4 to Laptop
                </a>
              ) : (
                <button
                  disabled
                  className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-900 px-3.5 py-2 text-xs text-zinc-400 opacity-70"
                >
                  <Download className="h-3.5 w-3.5" />
                  {isDemo && exp.status === "ready" ? "Demo master" : "Download unavailable"}
                </button>
              )}
            </div>
            {preview === exp.id && exp.status === "ready" && exp.download_url && (
              <video aria-label="Rendered master" controls crossOrigin="use-credentials" preload="metadata"
                src={`${exp.download_url}${exp.download_url.includes("?") ? "&" : "?"}inline=1`}
                className="max-h-[32rem] w-full rounded-lg bg-black" />
            )}
          </div>
        ))}
      </div>

      <div className="rounded-xl border border-zinc-800 bg-[#111216] p-5 space-y-4">
        <div className="flex items-center gap-2 text-sm font-bold text-amber-300">
          <Sparkles className="h-4 w-4" />
          Manual Upload Publish Kit (TikTok &amp; Facebook)
        </div>
        {[
          { k: "hook", label: "Opening Hook", val: latestKit.hook },
          { k: "title", label: "SEO Title", val: latestKit.seo_title },
          { k: "desc", label: "Description", val: latestKit.description },
          { k: "tags", label: "Hashtags", val: (latestKit.hashtags ?? []).join(" ") },
        ].map((item) => (
          <div key={item.k} className="flex items-start justify-between gap-3 rounded-lg border border-zinc-800 bg-zinc-900/50 p-3">
            <div className="text-xs">
              <div className="font-semibold text-zinc-400">{item.label}</div>
              <div className="mt-1 text-zinc-100">{item.val}</div>
            </div>
            <button
              aria-label={`Copy ${item.label}`}
              onClick={() => copyText(item.k, item.val)}
              className="shrink-0 rounded border border-zinc-700 bg-zinc-800 px-2.5 py-1 text-[11px] hover:bg-zinc-700"
            >
              {copiedKey === item.k ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <ClipboardCopy className="h-3.5 w-3.5" />}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

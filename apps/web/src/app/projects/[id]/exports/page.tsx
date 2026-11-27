"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Check, ClipboardCopy, Download, Film, HardDrive, Sparkles } from "lucide-react";
import type { ExportRecord, ProjectDetail, TargetResolution } from "@kinetiq/contracts";
import { api } from "@/lib/api-client";
import { StatusBadge } from "@/components/StatusBadge";

export default function ExportsPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [exportsList, setExportsList] = useState<ExportRecord[]>([]);
  const [isDemo, setIsDemo] = useState(false);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  async function load() {
    const [p, ex] = await Promise.all([api.getProject(projectId), api.listExports(projectId)]);
    setProject(p.data);
    setExportsList(ex.data);
    setIsDemo(p.demo || ex.demo);
  }

  useEffect(() => {
    load();
  }, [projectId]);

  async function handleStartExport(res: TargetResolution) {
    if (isDemo) {
      setMsg(`Demo Mode: Queued ${res.toUpperCase()} final master export preview.`);
      return;
    }
    await api.createExport(projectId, res);
    setMsg(`Queued ${res.toUpperCase()} render! Final file will sync to Google Drive & appear below.`);
    await load();
  }

  function copyText(key: string, value: string) {
    navigator.clipboard?.writeText(value).catch(() => {});
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 1500);
  }

  if (!project) {
    return <div className="p-8 text-sm text-zinc-400">Loading export center...</div>;
  }

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
            {project.title} · {project.aspect} · Master synced to Google Drive Vault
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
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-2.5 text-xs text-amber-200">
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
              onClick={() => handleStartExport("1080p")}
              className="rounded-lg bg-amber-500 px-4 py-2 text-xs font-semibold text-zinc-950 hover:bg-amber-400"
            >
              Render Native 1080p MP4
            </button>
            <button
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
                <StatusBadge value={exp.status} />
              </div>
              <div className="text-xs text-zinc-400">
                Revision {exp.revision} · Drive ID: {exp.drive_file_id ?? "local vault"}
              </div>
            </div>

            <div className="flex gap-2">
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
                  {exp.status === "ready" ? "Ready in Drive (Demo)" : "Rendering..."}
                </button>
              )}
            </div>
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

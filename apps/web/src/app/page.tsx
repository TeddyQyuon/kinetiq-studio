"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, ArrowRight, Cpu, Download, Film, Plus, Sparkles, Video } from "lucide-react";
import type { ProjectSummary, WorkerRecord } from "@kinetiq/contracts";
import { api } from "@/lib/api-client";
import { StatusBadge } from "@/components/StatusBadge";

export default function LibraryPage() {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [workers, setWorkers] = useState<WorkerRecord[]>([]);
  const [isDemo, setIsDemo] = useState(false);

  useEffect(() => {
    api.listProjects().then((res) => {
      setProjects(res.data);
      setIsDemo(res.demo);
    });
    api.listWorkers().then((res) => setWorkers(res.data));
  }, []);

  const totalNeedsReview = projects.reduce((acc, p) => acc + p.needs_review_count, 0);
  const onlineWorkers = workers.filter((w) => w.status !== "offline").length;
  const cuUsed = workers.reduce((acc, w) => acc + (w.cu_used_month || 0), 0);

  return (
    <div className="mx-auto max-w-6xl space-y-8 p-6 md:p-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Studio Library</h1>
          <p className="mt-1 text-sm text-zinc-400">
            Faceless 1080p &amp; 4K educational projects synced with your Google Drive Vault.
          </p>
        </div>
        <Link
          href="/create"
          className="inline-flex items-center gap-2 rounded-lg bg-amber-500 px-4 py-2.5 text-sm font-semibold text-zinc-950 transition hover:bg-amber-400"
        >
          <Plus className="h-4 w-4" />
          New Educational Video
        </Link>
      </div>

      {isDemo && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-xs text-amber-200">
          Running in <strong>Phase 0 Interactive Demo Mode</strong> because FastAPI is offline. Start{" "}
          <code className="rounded bg-black/30 px-1.5 py-0.5">uvicorn app.main:app --port 8000</code> to persist
          projects and connect Colab.
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-xl border border-zinc-800 bg-[#111216] p-4">
          <div className="text-xs text-zinc-400">Total Projects</div>
          <div className="mt-1.5 text-2xl font-bold">{projects.length}</div>
        </div>
        <div className="rounded-xl border border-zinc-800 bg-[#111216] p-4">
          <div className="text-xs text-zinc-400">Active Colab GPUs</div>
          <div className="mt-1.5 flex items-center gap-2 text-2xl font-bold">
            <Cpu className="h-5 w-5 text-emerald-400" />
            {onlineWorkers}
          </div>
        </div>
        <div className="rounded-xl border border-zinc-800 bg-[#111216] p-4">
          <div className="text-xs text-zinc-400">Est. Colab Budget</div>
          <div className="mt-1.5 text-2xl font-bold text-amber-300">{cuUsed.toFixed(1)} / 200 CU</div>
        </div>
        <div className="rounded-xl border border-zinc-800 bg-[#111216] p-4">
          <div className="text-xs text-zinc-400">HOTL QA Review Queue</div>
          <div className="mt-1.5 flex items-center gap-2 text-2xl font-bold">
            {totalNeedsReview > 0 ? (
              <span className="flex items-center gap-2 text-rose-300">
                <AlertTriangle className="h-5 w-5" />
                {totalNeedsReview}
              </span>
            ) : (
              <span className="text-emerald-300">0</span>
            )}
          </div>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Link
          href="/create?preset=reel_60s"
          className="group rounded-xl border border-zinc-800 bg-gradient-to-br from-[#14161d] to-[#0d0e12] p-5 transition hover:border-amber-500/40"
        >
          <div className="flex items-center justify-between">
            <span className="rounded-full bg-amber-500/15 px-2.5 py-1 text-[11px] font-semibold text-amber-300">
              9:16 VERTICAL · NATIVE 1080P
            </span>
            <ArrowRight className="h-4 w-4 text-zinc-500 transition group-hover:translate-x-1 group-hover:text-amber-400" />
          </div>
          <h2 className="mt-3 text-base font-bold">1-Minute Viral Reel (TikTok / FB Reels)</h2>
          <p className="mt-1 text-xs text-zinc-400">
            8–10 punchy scenes, Kokoro English voiceover, TikTok safe-zone framing, and kinetic word captions.
          </p>
        </Link>

        <Link
          href="/create?preset=lesson_10m"
          className="group rounded-xl border border-zinc-800 bg-gradient-to-br from-[#14161d] to-[#0d0e12] p-5 transition hover:border-cyan-500/40"
        >
          <div className="flex items-center justify-between">
            <span className="rounded-full bg-cyan-500/15 px-2.5 py-1 text-[11px] font-semibold text-cyan-300">
              16:9 WIDESCREEN · 1080P / 4K
            </span>
            <ArrowRight className="h-4 w-4 text-zinc-500 transition group-hover:translate-x-1 group-hover:text-cyan-400" />
          </div>
          <h2 className="mt-3 text-base font-bold">5–10+ Min Educational Lesson</h2>
          <p className="mt-1 text-xs text-zinc-400">
            Hybrid LTX-2.5/Wan 2.2 cinematic B-roll mixed with razor-sharp native 4K Remotion charts &amp; steps.
          </p>
        </Link>
      </div>

      <div className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-zinc-400">Your Projects</h2>
        <div className="grid gap-4 md:grid-cols-2">
          {projects.map((p) => {
            const pct = p.scene_count ? Math.round((p.ready_scenes / p.scene_count) * 100) : 0;
            return (
              <div key={p.id} className="rounded-xl border border-zinc-800 bg-[#111216] p-5 space-y-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded bg-zinc-800 px-2 py-0.5 text-[11px] font-semibold text-zinc-200">
                        {p.aspect}
                      </span>
                      <span className="rounded bg-zinc-800 px-2 py-0.5 text-[11px] font-semibold text-amber-300 uppercase">
                        {p.target_res}
                      </span>
                      <StatusBadge value={p.status} />
                    </div>
                    <h3 className="mt-2 text-base font-bold">{p.title}</h3>
                    <p className="mt-0.5 line-clamp-1 text-xs text-zinc-400">{p.topic}</p>
                  </div>
                  <div className="rounded-lg bg-zinc-900 p-2 text-zinc-400">
                    <Video className="h-5 w-5" />
                  </div>
                </div>

                <div>
                  <div className="flex justify-between text-xs text-zinc-400">
                    <span>
                      Scenes Ready: {p.ready_scenes}/{p.scene_count}
                    </span>
                    <span>{pct}%</span>
                  </div>
                  <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-zinc-800">
                    <div className="h-full rounded-full bg-emerald-500" style={{ width: `${pct}%` }} />
                  </div>
                  {p.needs_review_count > 0 && (
                    <div className="mt-2 text-xs text-rose-300">
                      {p.needs_review_count} scene(s) flagged by Auto-QA for your review
                    </div>
                  )}
                </div>

                <div className="flex flex-wrap gap-2 pt-1">
                  <Link
                    href={`/projects/${p.id}/plan`}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs font-medium hover:bg-zinc-800"
                  >
                    <Sparkles className="h-3.5 w-3.5 text-amber-400" />
                    Plan &amp; QA
                  </Link>
                  <Link
                    href={`/projects/${p.id}/editor`}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-amber-500 px-3 py-1.5 text-xs font-semibold text-zinc-950 hover:bg-amber-400"
                  >
                    <Film className="h-3.5 w-3.5" />
                    Open AI Editor
                  </Link>
                  <Link
                    href={`/projects/${p.id}/exports`}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs font-medium hover:bg-zinc-800"
                  >
                    <Download className="h-3.5 w-3.5 text-cyan-400" />
                    Export
                  </Link>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

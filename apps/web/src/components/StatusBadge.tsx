import React from "react";

const STATUS_STYLES: Record<string, string> = {
  ready: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  passed: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  online: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  human_approved: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  generating: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  generating_audio: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  generating_keyframe: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  generating_clip: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  qa_checking: "bg-cyan-500/15 text-cyan-300 border-cyan-500/30",
  auto_healing: "bg-violet-500/15 text-violet-300 border-violet-500/30",
  busy: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  needs_review: "bg-rose-500/20 text-rose-300 border-rose-500/40",
  failed: "bg-rose-500/20 text-rose-300 border-rose-500/40",
  planned: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  offline: "bg-zinc-700/30 text-zinc-400 border-zinc-700",
};

export function StatusBadge({ value }: { value: string }) {
  const cls = STATUS_STYLES[value] ?? "bg-zinc-800 text-zinc-300 border-zinc-700";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium uppercase tracking-wider ${cls}`}
    >
      {value.replace(/_/g, " ")}
    </span>
  );
}
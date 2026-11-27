"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, CheckCircle2, ClipboardCopy, ShieldCheck, Sparkles } from "lucide-react";
import type { BrainMode, QAPolicy, TargetResolution } from "@kinetiq/contracts";
import { api } from "@/lib/api-client";

export default function CreateProjectPage() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [topic, setTopic] = useState("");
  const [scriptOverride, setScriptOverride] = useState("");
  const [aspect, setAspect] = useState<"9:16" | "16:9">("16:9");
  const [targetRes, setTargetRes] = useState<TargetResolution>("1080p");
  const [durationPreset, setDurationPreset] = useState<"reel_60s" | "lesson_5m" | "lesson_10m">("reel_60s");
  const [qaPolicy, setQaPolicy] = useState<QAPolicy>("hotl_smart");
  const [brainMode, setBrainMode] = useState<BrainMode>("gemini_auto");
  const [chatgptJson, setChatgptJson] = useState("");
  const [briefText, setBriefText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim() || !topic.trim()) {
      setError("Please enter both a video title and topic.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const { data: project, demo } = await api.createProject({
        title: title.trim(),
        topic: topic.trim(),
        aspect,
        target_res: targetRes,
        duration_preset: durationPreset,
        qa_policy: qaPolicy,
        brain_mode: brainMode,
        script_override: scriptOverride.trim() || undefined,
      });
      if (!demo && brainMode === "chatgpt_bridge" && chatgptJson.trim()) {
        await api.importChatGPTPlan(project.id, chatgptJson);
      }
      router.push(`/projects/${project.id}/plan`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create project");
      setSubmitting(false);
    }
  }

  function handleCopyDirectorBrief() {
    const prompt = [
      "Act as an elite faceless educational video director (Vox / Kurzgesagt style).",
      `Title: ${title || "Educational Explainer"}`,
      `Topic: ${topic || "Explain core concept visually"}`,
      `Format: ${aspect} (${targetRes}), Preset: ${durationPreset}`,
      "Return ONLY raw JSON (no markdown) with fields: palette[], look, negative, hook, seo_title, description, hashtags[], and scenes[] where each scene has narration, duration_ms, kind ('ai_video'|'diagram'|'image_motion'), camera, model ('ltx25_fp8'|'wan22_i2v'|'qwen_image'), ltx25_prompt, qwen_image_prompt, and optional diagram_template/diagram_props.",
    ].join("\n");
    setBriefText(prompt);
    navigator.clipboard?.writeText(prompt).catch(() => {});
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6 md:p-8">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Create Faceless Educational Video</h1>
        <p className="mt-1 text-sm text-zinc-400">
          AI directs local Colab models (LTX-2.5, Wan 2.2, Qwen-Image) + native 1080p/4K Remotion diagrams.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6 rounded-2xl border border-zinc-800 bg-[#111216] p-6">
        {error && (
          <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-4 py-2.5 text-xs text-rose-200">
            {error}
          </div>
        )}

        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-zinc-300">Video Title</label>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g., Why Quantum Computers Break RSA"
              className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3.5 py-2.5 text-sm outline-none focus:border-amber-500"
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-zinc-300">Duration Preset</label>
            <select
              value={durationPreset}
              onChange={(e) => setDurationPreset(e.target.value as typeof durationPreset)}
              className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3.5 py-2.5 text-sm outline-none focus:border-amber-500"
            >
              <option value="reel_60s">1-Minute Reel (TikTok / FB Reels)</option>
              <option value="lesson_5m">5-Minute Educational Lesson</option>
              <option value="lesson_10m">10+ Minute Deep Dive</option>
            </select>
          </div>
        </div>

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-zinc-300">Topic &amp; Learning Goal</label>
          <textarea
            rows={2}
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="Explain the core intuition, key comparison numbers, and real-world takeaway..."
            className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3.5 py-2.5 text-sm outline-none focus:border-amber-500"
          />
        </div>

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-zinc-300">
            Optional English Script Override <span className="text-zinc-500">(leave blank for AI script)</span>
          </label>
          <textarea
            rows={3}
            value={scriptOverride}
            onChange={(e) => setScriptOverride(e.target.value)}
            placeholder="Paste an English narration script here to split cleanly into timed scenes..."
            className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3.5 py-2.5 text-sm outline-none focus:border-amber-500"
          />
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <label className="text-xs font-medium text-zinc-300">Aspect Ratio</label>
            <div className="grid grid-cols-2 gap-2.5">
              {(["9:16", "16:9"] as const).map((val) => (
                <button
                  type="button"
                  key={val}
                  onClick={() => setAspect(val)}
                  className={`rounded-lg border p-3 text-left text-xs transition ${
                    aspect === val
                      ? "border-amber-500 bg-amber-500/10 text-amber-200"
                      : "border-zinc-800 bg-zinc-900/60 text-zinc-400 hover:border-zinc-700"
                  }`}
                >
                  <div className="font-bold text-sm">{val}</div>
                  <div>{val === "9:16" ? "Reels / TikTok" : "FB Watch / YouTube"}</div>
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <label className="text-xs font-medium text-zinc-300">Target Resolution</label>
            <div className="grid grid-cols-3 gap-2">
              {(["720p", "1080p", "4k"] as const).map((res) => (
                <button
                  type="button"
                  key={res}
                  onClick={() => setTargetRes(res)}
                  className={`rounded-lg border p-3 text-center text-xs transition ${
                    targetRes === res
                      ? "border-cyan-500 bg-cyan-500/10 text-cyan-200"
                      : "border-zinc-800 bg-zinc-900/60 text-zinc-400 hover:border-zinc-700"
                  }`}
                >
                  <div className="font-bold uppercase">{res}</div>
                  <div className="text-[10px] text-zinc-400">
                    {res === "720p" ? "Draft" : res === "1080p" ? "Full HD" : "FlashVSR+"}
                  </div>
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <label className="flex items-center gap-1.5 text-xs font-medium text-zinc-300">
              <ShieldCheck className="h-3.5 w-3.5 text-emerald-400" />
              Vision QA Gate Policy
            </label>
            <select
              value={qaPolicy}
              onChange={(e) => setQaPolicy(e.target.value as QAPolicy)}
              className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3.5 py-2.5 text-sm outline-none focus:border-amber-500"
            >
              <option value="hotl_smart">Auto QA + Human-on-the-Loop (Recommended)</option>
              <option value="hitl_strict_gate">Strict Human Approval Before Every Video Clip</option>
              <option value="full_auto">Full Auto (Max 2 Retries, No Pause)</option>
            </select>
          </div>

          <div className="space-y-2">
            <label className="flex items-center gap-1.5 text-xs font-medium text-zinc-300">
              <Sparkles className="h-3.5 w-3.5 text-amber-400" />
              AI Director Brain
            </label>
            <select
              value={brainMode}
              onChange={(e) => setBrainMode(e.target.value as BrainMode)}
              className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3.5 py-2.5 text-sm outline-none focus:border-amber-500"
            >
              <option value="gemini_auto">Gemini Auto (Google AI Pro Credits)</option>
              <option value="chatgpt_bridge">ChatGPT Plus Co-Director (Copy/Paste JSON Bridge)</option>
              <option value="openai_api">OpenAI API (Optional Key)</option>
            </select>
          </div>
        </div>

        {brainMode === "chatgpt_bridge" && (
          <div className="space-y-3 rounded-xl border border-amber-500/30 bg-amber-500/5 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="text-xs font-semibold text-amber-200">
                ChatGPT Plus Co-Director Bridge (Zero Extra API Cost)
              </div>
              <button
                type="button"
                onClick={handleCopyDirectorBrief}
                className="inline-flex items-center gap-1.5 rounded-lg border border-amber-500/40 bg-amber-500/15 px-3 py-1.5 text-xs font-medium text-amber-200 hover:bg-amber-500/25"
              >
                <ClipboardCopy className="h-3.5 w-3.5" />
                Copy Director Brief
              </button>
            </div>
            {briefText && (
              <div className="rounded bg-black/40 p-2.5 text-[11px] text-zinc-300 font-mono whitespace-pre-wrap">
                {briefText}
              </div>
            )}
            <textarea
              rows={4}
              value={chatgptJson}
              onChange={(e) => setChatgptJson(e.target.value)}
              placeholder="Paste the JSON storyboard returned by ChatGPT Plus here (optional — you can also import it on the Plan page)..."
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-xs font-mono outline-none focus:border-amber-500"
            />
          </div>
        )}

        <div className="flex items-center justify-between border-t border-zinc-800 pt-4">
          <div className="flex items-center gap-2 text-xs text-zinc-400">
            <CheckCircle2 className="h-4 w-4 text-emerald-400" />
            Every finished scene auto-syncs to Google Drive Vault
          </div>
          <button
            type="submit"
            disabled={submitting}
            className="inline-flex items-center gap-2 rounded-lg bg-amber-500 px-5 py-2.5 text-sm font-semibold text-zinc-950 transition hover:bg-amber-400 disabled:opacity-50"
          >
            {submitting ? "Creating Plan..." : "Create Storyboard"}
            <ArrowRight className="h-4 w-4" />
          </button>
        </div>
      </form>
    </div>
  );
}

"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Player } from "@remotion/player";
import {
  Check,
  Download,
  Redo2,
  RefreshCw,
  Send,
  Sparkles,
  Undo2,
  Wand2,
  X,
} from "lucide-react";
import { EduComposition } from "@kinetiq/remotion-studio";
import type { CameraPreset, ModelChoice } from "@kinetiq/contracts";
import { api } from "@/lib/api-client";
import { useEditorStore } from "@/stores/editor-store";
import { StatusBadge } from "@/components/StatusBadge";

export default function VideoEditorPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;

  const {
    document,
    mediaUrls,
    selectedSceneId,
    undoStack,
    redoStack,
    pendingProposal,
    isDemo,
    hydrate,
    selectScene,
    dispatchLocalCommand,
    setPendingProposal,
    acceptPendingProposalLocal,
    undo,
    redo,
  } = useEditorStore();

  const [chatInput, setChatInput] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [narrationDraft, setNarrationDraft] = useState("");
  const [promptDraft, setPromptDraft] = useState("");

  useEffect(() => {
    api.getProject(projectId).then((res) => {
      hydrate(res.data.id, res.data.timeline, res.data.media_urls, res.demo);
    });
  }, [projectId, hydrate]);

  const selectedScene = document?.tracks.visual.find((s) => s.id === selectedSceneId) ?? null;

  useEffect(() => {
    if (selectedScene) {
      setNarrationDraft(selectedScene.narration);
      setPromptDraft(selectedScene.prompt?.ltx25 ?? selectedScene.prompt?.qwen_image ?? "");
    }
  }, [selectedScene?.id]);

  async function handleAskCopilot(e: React.FormEvent) {
    e.preventDefault();
    if (!chatInput.trim() || !document) return;
    setChatBusy(true);
    try {
      const proposal = await api.copilotChat(projectId, chatInput.trim(), selectedSceneId);
      setPendingProposal(proposal);
      setChatInput("");
    } finally {
      setChatBusy(false);
    }
  }

  async function handleAcceptProposal() {
    if (!document || !pendingProposal) return;
    if (!isDemo) {
      const updated = await api.acceptCopilot(
        projectId,
        pendingProposal.proposal_id,
        document.revision,
        true,
      );
      hydrate(projectId, updated, mediaUrls, false);
      setPendingProposal(null);
    } else {
      acceptPendingProposalLocal();
    }
  }

  async function handleUndo() {
    const revBefore = document?.revision ?? 1;
    const cmds = undo();
    if (cmds && !isDemo) {
      await api.applyCommands(projectId, revBefore, cmds);
    }
  }

  async function handleRedo() {
    const revBefore = document?.revision ?? 1;
    const cmds = redo();
    if (cmds && !isDemo) {
      await api.applyCommands(projectId, revBefore, cmds);
    }
  }

  async function handleSaveSceneInspector() {
    if (!document || !selectedScene) return;
    const cmds: Parameters<typeof api.applyCommands>[2] = [];
    if (narrationDraft !== selectedScene.narration) {
      cmds.push({ type: "edit_narration", scene_id: selectedScene.id, narration: narrationDraft });
    }
    if (promptDraft && promptDraft !== (selectedScene.prompt?.ltx25 ?? "")) {
      cmds.push({ type: "regenerate_clip", scene_id: selectedScene.id, prompt: promptDraft });
    }
    for (const c of cmds) {
      dispatchLocalCommand(c);
    }
    if (!isDemo && cmds.length) {
      await api.applyCommands(projectId, document.revision, cmds);
    }
  }

  if (!document) {
    return <div className="p-8 text-sm text-zinc-400">Loading browser timeline editor...</div>;
  }

  const totalMs = document.tracks.visual.reduce((max, s) => Math.max(max, s.end_ms), 6000);
  const durationInFrames = Math.max(30, Math.round((totalMs / 1000) * document.format.fps));

  return (
    <div className="flex h-screen flex-col bg-[#09090b]">
      {/* Top Toolbar */}
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800 bg-[#0d0e12] px-4 py-2.5">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-xs text-zinc-400 hover:text-zinc-200">
            ← Library
          </Link>
          <span className="text-sm font-bold">Editor · rev {document.revision}</span>
          <span className="rounded bg-zinc-800 px-2 py-0.5 text-[11px]">{document.format.aspect}</span>
          <span className="rounded bg-zinc-800 px-2 py-0.5 text-[11px] text-amber-300 uppercase">
            {document.format.target}
          </span>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={handleUndo}
            disabled={undoStack.length === 0}
            className="inline-flex items-center gap-1 rounded-lg border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-xs disabled:opacity-40 hover:bg-zinc-800"
          >
            <Undo2 className="h-3.5 w-3.5" />
            Undo
          </button>
          <button
            onClick={handleRedo}
            disabled={redoStack.length === 0}
            className="inline-flex items-center gap-1 rounded-lg border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-xs disabled:opacity-40 hover:bg-zinc-800"
          >
            <Redo2 className="h-3.5 w-3.5" />
            Redo
          </button>
          <Link
            href={`/projects/${projectId}/exports`}
            className="inline-flex items-center gap-1.5 rounded-lg bg-amber-500 px-3.5 py-1.5 text-xs font-semibold text-zinc-950 hover:bg-amber-400"
          >
            <Download className="h-3.5 w-3.5" />
            Export &amp; Download
          </Link>
        </div>
      </header>

      {/* Main 3-Pane Studio */}
      <div className="grid flex-1 min-h-0 grid-cols-1 lg:grid-cols-12">
        {/* Left: Scene List & Inspector */}
        <div className="lg:col-span-3 flex flex-col border-r border-zinc-800 bg-[#0d0e12] min-h-0 overflow-y-auto">
          <div className="border-b border-zinc-800 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-zinc-400">
            Scenes ({document.tracks.visual.length})
          </div>
          <div className="space-y-1.5 p-2.5">
            {document.tracks.visual.map((s, idx) => (
              <button
                key={s.id}
                onClick={() => selectScene(s.id)}
                className={`w-full rounded-lg border p-2.5 text-left transition ${
                  selectedSceneId === s.id
                    ? "border-amber-500 bg-amber-500/10"
                    : "border-zinc-800 bg-zinc-900/50 hover:border-zinc-700"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[11px] text-zinc-400">
                    #{String(idx + 1).padStart(2, "0")} · {((s.end_ms - s.start_ms) / 1000).toFixed(1)}s
                  </span>
                  <StatusBadge value={s.status} />
                </div>
                <p className="mt-1 line-clamp-2 text-xs text-zinc-200">{s.narration}</p>
              </button>
            ))}
          </div>

          {selectedScene && (
            <div className="mt-auto border-t border-zinc-800 p-3 space-y-2.5 bg-[#111216]">
              <div className="text-xs font-semibold text-amber-300">Scene Inspector ({selectedScene.id})</div>
              <textarea
                rows={2}
                value={narrationDraft}
                onChange={(e) => setNarrationDraft(e.target.value)}
                className="w-full rounded border border-zinc-700 bg-zinc-900 p-2 text-xs outline-none focus:border-amber-500"
                placeholder="English narration..."
              />
              {selectedScene.kind !== "diagram" && (
                <>
                  <textarea
                    rows={2}
                    value={promptDraft}
                    onChange={(e) => setPromptDraft(e.target.value)}
                    className="w-full rounded border border-zinc-700 bg-zinc-900 p-2 text-xs outline-none focus:border-amber-500"
                    placeholder="Visual prompt..."
                  />
                  <div className="grid grid-cols-2 gap-1.5">
                    <select
                      value={selectedScene.model ?? "ltx25_fp8"}
                      onChange={(e) =>
                        dispatchLocalCommand({
                          type: "regenerate_clip",
                          scene_id: selectedScene.id,
                          model: e.target.value as ModelChoice,
                        })
                      }
                      className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-[11px]"
                    >
                      <option value="ltx25_fp8">LTX-2.5 FP8</option>
                      <option value="wan22_i2v">Wan 2.2 Fast</option>
                      <option value="qwen_image">Qwen-Image</option>
                    </select>
                    <select
                      value={selectedScene.camera ?? "slow_dolly_in"}
                      onChange={(e) =>
                        dispatchLocalCommand({
                          type: "regenerate_clip",
                          scene_id: selectedScene.id,
                          camera: e.target.value as CameraPreset,
                        })
                      }
                      className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-[11px]"
                    >
                      <option value="slow_dolly_in">Slow Dolly In</option>
                      <option value="cinematic_orbit">Cinematic Orbit</option>
                      <option value="aerial_pullback">Aerial Pullback</option>
                      <option value="macro_pan">Macro Pan</option>
                      <option value="static">Static</option>
                    </select>
                  </div>
                </>
              )}
              <button
                onClick={handleSaveSceneInspector}
                className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-zinc-800 py-1.5 text-xs font-semibold hover:bg-zinc-700"
              >
                <RefreshCw className="h-3.5 w-3.5 text-amber-400" />
                Apply Scene Edit
              </button>
            </div>
          )}
        </div>

        {/* Center: @remotion/player Preview Stage */}
        <div className="lg:col-span-6 flex flex-col items-center justify-center bg-[#09090b] p-4 min-h-[340px]">
          <div
            className="relative flex items-center justify-center overflow-hidden rounded-xl border border-zinc-800 bg-black shadow-2xl"
            style={{
              width: "100%",
              maxWidth: document.format.aspect === "9:16" ? 310 : 720,
              aspectRatio: document.format.aspect === "9:16" ? "9 / 16" : "16 / 9",
            }}
          >
            <Player
              component={EduComposition}
              inputProps={{ document, mediaUrls }}
              durationInFrames={durationInFrames}
              compositionWidth={document.format.width}
              compositionHeight={document.format.height}
              fps={document.format.fps}
              controls
              style={{ width: "100%", height: "100%" }}
            />
          </div>
          <div className="mt-2 text-[11px] text-zinc-500">
            Unified Remotion Engine — browser preview matches final {document.format.target.toUpperCase()} export 1:1
          </div>
        </div>

        {/* Right: AI Editing Copilot */}
        <div className="lg:col-span-3 flex flex-col border-l border-zinc-800 bg-[#0d0e12] min-h-0">
          <div className="flex items-center gap-2 border-b border-zinc-800 px-3.5 py-2.5 text-xs font-semibold text-amber-300">
            <Sparkles className="h-4 w-4" />
            AI Editing Copilot (Gemini + MCP)
          </div>

          <div className="flex-1 space-y-3 overflow-y-auto p-3 text-xs">
            <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3 text-zinc-300">
              Ask me to tighten pauses, insert a native 4K chart, rewrite a narration beat, or re-roll a scene with a
              new camera move. Every edit shows a preview before applying.
            </div>

            <div className="flex flex-wrap gap-1.5">
              {[
                "Tighten pauses for Reels",
                "Insert comparison chart",
                "Redo selected scene with orbit",
              ].map((quick) => (
                <button
                  key={quick}
                  onClick={() => setChatInput(quick)}
                  className="rounded-full border border-zinc-700 bg-zinc-900 px-2.5 py-1 text-[11px] text-zinc-300 hover:border-amber-500/50"
                >
                  {quick}
                </button>
              ))}
            </div>

            {pendingProposal && (
              <div className="space-y-2.5 rounded-xl border border-amber-500/40 bg-amber-500/10 p-3">
                <div className="flex items-center gap-1.5 font-semibold text-amber-200">
                  <Wand2 className="h-3.5 w-3.5" />
                  Proposed Edit ({pendingProposal.commands.length} command(s))
                </div>
                <p className="text-zinc-200">{pendingProposal.assistant_message}</p>
                <div className="rounded bg-black/40 p-2 font-mono text-[10px] text-zinc-300">
                  {pendingProposal.commands.map((c, i) => (
                    <div key={i}>• {c.type}</div>
                  ))}
                </div>
                <div className="text-[11px] text-amber-300">
                  Est. GPU Cost: {pendingProposal.est_cu.toFixed(2)} CU
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={handleAcceptProposal}
                    className="inline-flex flex-1 items-center justify-center gap-1 rounded-lg bg-emerald-500 py-1.5 font-semibold text-zinc-950 hover:bg-emerald-400"
                  >
                    <Check className="h-3.5 w-3.5" />
                    Accept
                  </button>
                  <button
                    onClick={() => setPendingProposal(null)}
                    className="inline-flex items-center justify-center gap-1 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-zinc-300 hover:bg-zinc-800"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            )}
          </div>

          <form onSubmit={handleAskCopilot} className="border-t border-zinc-800 p-3 flex gap-2">
            <input
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              placeholder="e.g., Add a chart after scene 1..."
              className="flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-xs outline-none focus:border-amber-500"
            />
            <button
              type="submit"
              disabled={chatBusy}
              className="rounded-lg bg-amber-500 px-3 py-2 text-zinc-950 hover:bg-amber-400 disabled:opacity-50"
            >
              <Send className="h-3.5 w-3.5" />
            </button>
          </form>
        </div>
      </div>

      {/* Bottom Multi-Track Timeline */}
      <div className="border-t border-zinc-800 bg-[#0d0e12] px-4 py-3 space-y-2">
        <div className="flex items-center gap-3 text-[11px]">
          <span className="w-16 shrink-0 font-semibold text-zinc-400">VISUALS</span>
          <div className="flex flex-1 gap-1 overflow-x-auto">
            {document.tracks.visual.map((s) => {
              const pct = Math.max(8, ((s.end_ms - s.start_ms) / totalMs) * 100);
              return (
                <button
                  key={s.id}
                  onClick={() => selectScene(s.id)}
                  style={{ width: `${pct}%` }}
                  className={`shrink-0 truncate rounded px-2 py-1.5 text-left text-[11px] font-medium border ${
                    selectedSceneId === s.id
                      ? "border-amber-400 bg-amber-500/25 text-amber-100"
                      : s.kind === "diagram"
                        ? "border-cyan-500/30 bg-cyan-500/15 text-cyan-200"
                        : "border-zinc-700 bg-zinc-800/80 text-zinc-200"
                  }`}
                >
                  {s.id} · {s.kind === "diagram" ? s.template : s.model ?? "ai"}
                </button>
              );
            })}
          </div>
        </div>

        <div className="flex items-center gap-3 text-[11px]">
          <span className="w-16 shrink-0 font-semibold text-zinc-400">CAPTIONS</span>
          <div className="flex-1 rounded border border-zinc-800 bg-zinc-900/70 px-3 py-1 text-zinc-400">
            {document.tracks.captions.enabled
              ? `Kinetic Captions ON (${document.tracks.captions.style} · ${document.tracks.captions.accent_color})`
              : "Captions Disabled"}
          </div>
        </div>

        <div className="flex items-center gap-3 text-[11px]">
          <span className="w-16 shrink-0 font-semibold text-zinc-400">VOICE</span>
          <div className="flex-1 rounded border border-emerald-500/20 bg-emerald-500/10 px-3 py-1 text-emerald-300">
            {document.voice.engine} · {document.voice.voice_id} ({document.voice.speed}x) · Music auto-duck{" "}
            {document.tracks.music.duck_db} dB
          </div>
        </div>
      </div>
    </div>
  );
}

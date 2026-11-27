"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Player, type PlayerRef } from "@remotion/player";
import {
  AlertTriangle,
  Check,
  Database,
  Download,
  GripVertical,
  HardDrive,
  Radio,
  Redo2,
  RefreshCw,
  RotateCcw,
  Scissors,
  Send,
  Sparkles,
  Trash2,
  Undo2,
  Wand2,
  X,
} from "lucide-react";
import { EduComposition } from "@kinetiq/remotion-studio";
import type {
  CameraPreset,
  DiagramTemplate,
  ModelChoice,
  StudioEvent,
  TimelineCommand,
  TimelineDocument,
} from "@kinetiq/contracts";
import { ApiError, api, type MusicPreset } from "@/lib/api-client";
import {
  clearOpfsProxyCache,
  getOpfsCacheStats,
  warmProjectProxyCache,
  type OpfsCacheStats,
} from "@/lib/opfs-proxy-cache";
import { useProjectEvents } from "@/lib/use-project-events";
import { useEditorStore } from "@/stores/editor-store";
import { AudioInspector, CaptionInspector, inspectorButton } from "./TrackInspector";
import { RevisionDrawer } from "./RevisionDrawer";
import { StatusBadge } from "@/components/StatusBadge";

interface TrimDragState {
  sceneId: string;
  edge: "start" | "end";
  startClientX: number;
  origStartMs: number;
  origEndMs: number;
  previewStartMs: number;
  previewEndMs: number;
  msPerPx: number;
}

type QueuedServerOp =
  | { kind: "commands"; commands: TimelineCommand[] }
  | {
      kind: "regenerate";
      sceneId: string;
      patch: { prompt?: string; model?: ModelChoice; camera?: CameraPreset; seed?: number };
    }
  | { kind: "accept_proposal"; proposalId: string; confirmSpend: boolean };

function formatTimeMs(ms: number): string {
  const clamped = Math.max(0, ms);
  const totalSec = clamped / 1000;
  const mins = Math.floor(totalSec / 60);
  const secs = (totalSec % 60).toFixed(1).padStart(4, "0");
  return `${String(mins).padStart(2, "0")}:${secs}`;
}

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 MB";
  const mb = bytes / (1024 * 1024);
  return `${mb.toFixed(mb >= 10 ? 0 : 1)} MB`;
}

function makeUniqueSplitId(baseId: string, existingIds: Set<string>): string {
  let suffix = 2;
  let candidate = `${baseId}_s${suffix}`;
  while (existingIds.has(candidate)) {
    suffix += 1;
    candidate = `${baseId}_s${suffix}`;
  }
  return candidate;
}

export default function VideoEditorPage() {
  const params = useParams<{ id: string }>();
  return <ProjectEditor key={params.id} projectId={params.id} />;
}

function ProjectEditor({ projectId }: { projectId: string }) {
  const {
    document,
    mediaUrls,
    opfsCachedAssets,
    selectedSceneId,
    undoStack,
    redoStack,
    pendingProposal,
    isDemo,
    dirty,
    autosaveStatus,
    restoredFromDraft,
    syncStatus,
    syncError,
    sceneProgress,
    hydrateWithAutosave,
    selectScene,
    dispatchLocalCommand,
    dispatchLocalBatch,
    setPendingProposal,
    acceptPendingProposalLocal,
    undo,
    redo,
    advanceServerRevision,
    reconcileServerSnapshot,
    markSyncedFromServer,
    setSyncState,
    discardLocalDraft,
    applyLiveEvent,
    mergeMediaUrls,
    setOpfsCachedAsset,
    clearOpfsCachedAssets,
  } = useEditorStore();

  const [inspector, setInspector] = useState<"Scene" | "Audio & Music" | "Captions & Overlays">("Scene");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const [draftReview, setDraftReview] = useState("");
  const [mediaBusy, setMediaBusy] = useState(false);
  const [loadError, setLoadError] = useState("");
  const mediaBusyRef = useRef(false);
  const mounted = useRef(false);
  const [chatInput, setChatInput] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [confirmSpend, setConfirmSpend] = useState(false);
  const [narrationDraft, setNarrationDraft] = useState("");
  const [promptDraft, setPromptDraft] = useState("");
  const [playheadMs, setPlayheadMs] = useState(0);
  const [draggedSceneId, setDraggedSceneId] = useState<string | null>(null);
  const [dropTargetSceneId, setDropTargetSceneId] = useState<string | null>(null);
  const [trimDrag, setTrimDrag] = useState<TrimDragState | null>(null);
  const [opfsStats, setOpfsStats] = useState<OpfsCacheStats | null>(null);

  const playerRef = useRef<PlayerRef>(null);
  const visualTrackRef = useRef<HTMLDivElement>(null);
  const rulerRef = useRef<HTMLDivElement>(null);
  const scrubbingRulerRef = useRef(false);
  const pendingOpsRef = useRef<QueuedServerOp[]>([]);
  const drainingOpsRef = useRef(false);

  // Initial load with crash-safe IndexedDB draft restoration
  useEffect(() => {
    let active = true;
    mounted.current = true;
    pendingOpsRef.current = [];
    api.getProject(projectId).then((res) => {
      if (!active) return;
      return hydrateWithAutosave(res.data.id, res.data.timeline, res.data.media_urls, res.demo, () => active);
    }).catch((err: unknown) => {
      if (active) setLoadError(err instanceof Error ? err.message : "Could not load this project.");
    });
    void getOpfsCacheStats().then((stats) => {
      if (active) setOpfsStats(stats);
    });
    return () => {
      active = false;
      mounted.current = false;
      pendingOpsRef.current = [];
    };
  }, [projectId, hydrateWithAutosave]);

  const selectedScene =
    document?.tracks.visual.find((s) => s.id === selectedSceneId) ?? null;

  useEffect(() => {
    if (selectedScene) {
      setNarrationDraft(selectedScene.narration);
      setPromptDraft(selectedScene.prompt?.ltx25 ?? selectedScene.prompt?.qwen_image ?? "");
    }
  }, [selectedScene?.id, selectedScene?.narration, selectedScene?.prompt?.ltx25, selectedScene?.prompt?.qwen_image]);

  // Reset spend confirmation checkbox whenever proposal changes
  useEffect(() => {
    setConfirmSpend(false);
  }, [pendingProposal?.proposal_id]);

  // Live SSE EventSource sync (disabled in offline demo mode)
  const handleLiveEvent = useCallback(
    (event: StudioEvent) => {
      applyLiveEvent(event);
      if ((event.event_type === "asset.synced" || event.event_type === "timeline.updated") && !isDemo) {
        void api.getProject(projectId).then((res) => {
          if (!mounted.current || res.demo || res.data.id !== projectId) return;
          mergeMediaUrls(res.data.media_urls);
          if (!mediaBusyRef.current && !drainingOpsRef.current && pendingOpsRef.current.length === 0) {
            reconcileServerSnapshot(res.data.timeline);
          }
        }).catch((err: unknown) => {
          if (mounted.current) setNotice(err instanceof Error ? err.message : "Could not refresh the preview.");
        });
      }
    },
    [applyLiveEvent, isDemo, mergeMediaUrls, projectId, reconcileServerSnapshot],
  );

  const { status: sseStatus, lastEventId, reconnectNow } = useProjectEvents({
    projectId,
    enabled: !isDemo && document?.project_id === projectId,
    onEvent: handleLiveEvent,
  });

  // Warm OPFS 540p proxy cache whenever document or mediaUrls update
  useEffect(() => {
    if (!document) return;
    const controller = new AbortController();
    void warmProjectProxyCache(document, mediaUrls, {
      signal: controller.signal,
      onAssetCached: (assetId, blobUrl) => {
        setOpfsCachedAsset(assetId, blobUrl);
      },
    }).then(() => {
      if (!controller.signal.aborted) {
        void getOpfsCacheStats().then(setOpfsStats);
      }
    });
    return () => {
      controller.abort();
    };
  }, [document, mediaUrls, setOpfsCachedAsset]);

  const effectiveMediaUrls = useMemo(
    () => ({ ...mediaUrls, ...opfsCachedAssets }),
    [mediaUrls, opfsCachedAssets],
  );

  const totalMs = useMemo(() => {
    if (!document) return 6000;
    return document.tracks.visual.reduce((max, s) => Math.max(max, s.end_ms), 6000);
  }, [document]);

  const fps = document?.format.fps ?? 30;
  const durationInFrames = Math.max(30, Math.round((totalMs / 1000) * fps));

  // Sync playheadMs with @remotion/player frameupdate and seeked events
  useEffect(() => {
    const player = playerRef.current;
    if (!player || !document) return;

    const handleFrameUpdate = (ev: { detail: { frame: number } }) => {
      setPlayheadMs(Math.round((ev.detail.frame / fps) * 1000));
    };
    const handleSeeked = (ev: { detail: { frame: number } }) => {
      setPlayheadMs(Math.round((ev.detail.frame / fps) * 1000));
    };

    player.addEventListener("frameupdate", handleFrameUpdate);
    player.addEventListener("seeked", handleSeeked);
    return () => {
      player.removeEventListener("frameupdate", handleFrameUpdate);
      player.removeEventListener("seeked", handleSeeked);
    };
  }, [Boolean(document), fps]);

  const seekToMs = useCallback(
    (ms: number) => {
      const clamped = Math.max(0, Math.min(totalMs, Math.round(ms)));
      setPlayheadMs(clamped);
      const frame = Math.max(0, Math.min(durationInFrames - 1, Math.round((clamped / 1000) * fps)));
      playerRef.current?.seekTo(frame);
    },
    [durationInFrames, fps, totalMs],
  );

  const drainWriteQueue = useCallback(async () => {
    if (drainingOpsRef.current || isDemo || mediaBusyRef.current) return;
    drainingOpsRef.current = true;
    try {
      while (pendingOpsRef.current.length > 0) {
        const op = pendingOpsRef.current[0];
        const expectedRev = useEditorStore.getState().serverRevision;
        setSyncState("syncing", null);
        try {
          if (op.kind === "commands") {
            const updated = await api.applyCommands(projectId, expectedRev, op.commands);
            if (!mounted.current) return;
            pendingOpsRef.current.shift();
            if (pendingOpsRef.current.length === 0) {
              markSyncedFromServer(updated);
            } else {
              advanceServerRevision(updated.revision);
            }
          } else if (op.kind === "regenerate") {
            const current = await api.getProject(projectId);
            if (current.data.timeline.revision !== expectedRev) {
              throw new ApiError(
                `Server revision changed (${current.data.timeline.revision} vs expected ${expectedRev}).`,
                409,
              );
            }
            await api.regenerateScene(projectId, op.sceneId, op.patch, expectedRev);
            const fresh = await api.getProject(projectId);
            if (!mounted.current) return;
            pendingOpsRef.current.shift();
            mergeMediaUrls(fresh.data.media_urls);
            if (pendingOpsRef.current.length === 0) {
              markSyncedFromServer(fresh.data.timeline);
            } else {
              advanceServerRevision(fresh.data.timeline.revision);
            }
          } else {
            const updated = await api.acceptCopilot(
              projectId,
              op.proposalId,
              expectedRev,
              op.confirmSpend,
            );
            if (!mounted.current) return;
            pendingOpsRef.current.shift();
            setPendingProposal(null);
            if (pendingOpsRef.current.length === 0) {
              markSyncedFromServer(updated);
            } else {
              advanceServerRevision(updated.revision);
            }
          }
        } catch (err) {
          if (!mounted.current) return;
          if (err instanceof ApiError && err.status === 409) {
            setSyncState(
              "conflict",
              "Server revision changed (409 Conflict). Sync draft or reload server.",
            );
          } else {
            setSyncState(
              "offline",
              err instanceof Error ? err.message : "Saved locally; server sync pending.",
            );
          }
          break;
        }
      }
    } finally {
      drainingOpsRef.current = false;
    }
  }, [
    advanceServerRevision,
    isDemo,
    markSyncedFromServer,
    mergeMediaUrls,
    projectId,
    setPendingProposal,
    setSyncState,
  ]);

  const enqueueCommandBatch = useCallback((commands: TimelineCommand[]) => {
    let buffer: TimelineCommand[] = [];
    const flush = () => {
      if (buffer.length > 0) {
        pendingOpsRef.current.push({ kind: "commands", commands: buffer });
        buffer = [];
      }
    };
    for (const cmd of commands) {
      if (cmd.type === "regenerate_clip") {
        flush();
        pendingOpsRef.current.push({
          kind: "regenerate",
          sceneId: cmd.scene_id,
          patch: {
            prompt: cmd.prompt,
            model: cmd.model,
            camera: cmd.camera,
            seed: cmd.seed,
          },
        });
      } else {
        buffer.push(cmd);
      }
    }
    flush();
  }, []);

  // Unified local + IndexedDB + serialized backend command commit
  const commitTimelineCommands = useCallback(
    async (commands: TimelineCommand[], label?: string) => {
      if (!document || commands.length === 0 || mediaBusyRef.current) return;
      if (!dispatchLocalBatch(commands, label)) return;
      if (isDemo) return;
      enqueueCommandBatch(commands);
      await drainWriteQueue();
    },
    [dispatchLocalBatch, document, drainWriteQueue, enqueueCommandBatch, isDemo],
  );

  const triggerSceneRegeneration = useCallback(
    async (
      sceneId: string,
      patch: { prompt?: string; model?: ModelChoice; camera?: CameraPreset },
      label?: string,
    ) => {
      if (!document || mediaBusyRef.current) return;
      if (!dispatchLocalCommand({ type: "regenerate_clip", scene_id: sceneId, ...patch }, label)) return;
      if (isDemo) return;
      pendingOpsRef.current.push({ kind: "regenerate", sceneId, patch });
      await drainWriteQueue();
    },
    [dispatchLocalCommand, document, drainWriteQueue, isDemo],
  );

  const handleUndo = useCallback(async () => {
    if (mediaBusyRef.current) return;
    const cmds = undo();
    if (!cmds || isDemo) return;
    enqueueCommandBatch(cmds);
    await drainWriteQueue();
  }, [drainWriteQueue, enqueueCommandBatch, isDemo, undo]);

  const handleRedo = useCallback(async () => {
    if (mediaBusyRef.current) return;
    const cmds = redo();
    if (!cmds || isDemo) return;
    enqueueCommandBatch(cmds);
    await drainWriteQueue();
  }, [drainWriteQueue, enqueueCommandBatch, isDemo, redo]);

  const activeSceneAtPlayhead = useMemo(() => {
    if (!document) return null;
    return (
      document.tracks.visual.find((s) => playheadMs >= s.start_ms && playheadMs < s.end_ms) ??
      selectedScene
    );
  }, [document, playheadMs, selectedScene]);

  const canSplitAtPlayhead = Boolean(
    activeSceneAtPlayhead && activeSceneAtPlayhead.end_ms - activeSceneAtPlayhead.start_ms >= 1000,
  );

  const handleSplitAtPlayhead = useCallback(async () => {
    if (!document || !activeSceneAtPlayhead) return;
    const dur = activeSceneAtPlayhead.end_ms - activeSceneAtPlayhead.start_ms;
    if (dur < 1000) return;

    const minSplit = activeSceneAtPlayhead.start_ms + 500;
    const maxSplit = activeSceneAtPlayhead.end_ms - 500;
    const splitAtMs =
      playheadMs >= minSplit && playheadMs <= maxSplit
        ? Math.round(playheadMs)
        : Math.round((activeSceneAtPlayhead.start_ms + activeSceneAtPlayhead.end_ms) / 2);

    const existingIds = new Set(document.tracks.visual.map((s) => s.id));
    const newSceneId = makeUniqueSplitId(activeSceneAtPlayhead.id, existingIds);

    await commitTimelineCommands(
      [
        {
          type: "split_scene",
          scene_id: activeSceneAtPlayhead.id,
          split_at_ms: splitAtMs,
          new_scene_id: newSceneId,
        },
      ],
      `Split ${activeSceneAtPlayhead.id}`,
    );
    selectScene(newSceneId);
  }, [activeSceneAtPlayhead, commitTimelineCommands, document, playheadMs, selectScene]);

  // Keyboard editing and transport share the same handlers as the toolbar.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      if (e.defaultPrevented || e.repeat || historyOpen || mediaBusyRef.current || tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target?.isContentEditable) {
        return;
      }

      const mod = e.metaKey || e.ctrlKey;
      if (!mod && e.code === "Space" && !e.altKey) {
        e.preventDefault();
        playerRef.current?.toggle();
      } else if (mod && e.key.toLowerCase() === "z" && !e.shiftKey) {
        e.preventDefault();
        void handleUndo();
      } else if (
        (mod && e.key.toLowerCase() === "z" && e.shiftKey) ||
        (mod && e.key.toLowerCase() === "y")
      ) {
        e.preventDefault();
        void handleRedo();
      } else if (!mod && e.key.toLowerCase() === "s") {
        e.preventDefault();
        if (selectedScene && selectedScene.id === activeSceneAtPlayhead?.id &&
            playheadMs >= selectedScene.start_ms + 500 && playheadMs <= selectedScene.end_ms - 500) {
          void handleSplitAtPlayhead();
        }
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [activeSceneAtPlayhead, handleRedo, handleSplitAtPlayhead, handleUndo, historyOpen, playheadMs, selectedScene]);

  // Edge-trim pointer drag handlers
  useEffect(() => {
    if (!trimDrag) return;

    const onPointerMove = (ev: PointerEvent) => {
      const dx = ev.clientX - trimDrag.startClientX;
      const rawDeltaMs = dx * trimDrag.msPerPx;
      const snappedDelta = Math.round(rawDeltaMs / 100) * 100;

      if (trimDrag.edge === "start") {
        const nextStart = Math.max(
          0,
          Math.min(trimDrag.origEndMs - 1000, trimDrag.origStartMs + snappedDelta),
        );
        setTrimDrag((prev) => (prev ? { ...prev, previewStartMs: nextStart } : null));
      } else {
        const nextEnd = Math.max(
          trimDrag.origStartMs + 1000,
          Math.min(trimDrag.origStartMs + 60000, trimDrag.origEndMs + snappedDelta),
        );
        setTrimDrag((prev) => (prev ? { ...prev, previewEndMs: nextEnd } : null));
      }
    };

    const onPointerUp = () => {
      const current = trimDrag;
      setTrimDrag(null);
      if (!current) return;
      if (
        current.previewStartMs !== current.origStartMs ||
        current.previewEndMs !== current.origEndMs
      ) {
        void commitTimelineCommands(
          [
            {
              type: "trim_scene",
              scene_id: current.sceneId,
              start_ms: current.previewStartMs,
              end_ms: current.previewEndMs,
            },
            { type: "tighten_pauses", max_gap_ms: 0 },
          ],
          `Trim ${current.sceneId}`,
        );
      }
    };

    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
    };
  }, [commitTimelineCommands, trimDrag]);

  async function askCopilot(message: string) {
    if (!message || !document || chatBusy) return;
    setChatBusy(true);
    try {
      const proposal = await api.copilotChat(projectId, message, selectedSceneId);
      if (!mounted.current) return;
      if (message === "review_draft" || !proposal.commands.length) {
        setDraftReview(proposal.assistant_message);
      } else {
        setPendingProposal(proposal);
      }
      if (message !== "review_draft") setChatInput("");
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Could not reach Copilot.");
    } finally {
      setChatBusy(false);
    }
  }

  async function handleAcceptProposal() {
    if (!document || !pendingProposal || mediaBusyRef.current || drainingOpsRef.current) return;
    const needsSpendConfirm = pendingProposal.requires_confirmation || pendingProposal.est_cu > 1.0;
    if (needsSpendConfirm && !confirmSpend) return;

    if (!isDemo) {
      pendingOpsRef.current.push({
        kind: "accept_proposal",
        proposalId: pendingProposal.proposal_id,
        confirmSpend: true,
      });
      await drainWriteQueue();
    } else {
      acceptPendingProposalLocal();
    }
  }

  async function handleSaveSceneInspector() {
    if (!document || !selectedScene) return;
    if (narrationDraft !== selectedScene.narration) {
      await commitTimelineCommands(
        [{ type: "edit_narration", scene_id: selectedScene.id, narration: narrationDraft }],
        `Edit ${selectedScene.id}`,
      );
    }
    if (selectedScene.kind !== "diagram" && promptDraft && promptDraft !== (selectedScene.prompt?.ltx25 ?? selectedScene.prompt?.qwen_image ?? "")) {
      await triggerSceneRegeneration(
        selectedScene.id,
        { prompt: promptDraft },
        `Prompt ${selectedScene.id}`,
      );
    }
  }

  async function handleQuickTrimDelta(deltaMs: number) {
    if (!selectedScene) return;
    const nextEnd = Math.max(selectedScene.start_ms + 1000, selectedScene.end_ms + deltaMs);
    if (nextEnd === selectedScene.end_ms) return;
    await commitTimelineCommands(
      [
        {
          type: "trim_scene",
          scene_id: selectedScene.id,
          start_ms: selectedScene.start_ms,
          end_ms: nextEnd,
        },
        { type: "tighten_pauses", max_gap_ms: 0 },
      ],
      `Trim ${selectedScene.id}`,
    );
  }

  async function handleReorderScenes(sourceId: string, targetId: string) {
    if (!document || !sourceId || !targetId || sourceId === targetId) return;
    const fromIdx = document.tracks.visual.findIndex((s) => s.id === sourceId);
    const toIdx = document.tracks.visual.findIndex((s) => s.id === targetId);
    if (fromIdx === -1 || toIdx === -1 || fromIdx === toIdx) return;
    await commitTimelineCommands(
      [{ type: "move_scene", scene_id: sourceId, to_index: toIdx }],
      `Move ${sourceId}`,
    );
  }

  async function handleSyncDraftToServer() {
    if (!document || drainingOpsRef.current || mediaBusyRef.current) return;
    setSyncState("syncing", null);
    if (pendingOpsRef.current.length > 0) {
      try {
        const latest = await api.getProject(projectId);
        if (!mounted.current) return;
        mergeMediaUrls(latest.data.media_urls);
        advanceServerRevision(latest.data.timeline.revision);
        await drainWriteQueue();
      } catch (err) {
        setSyncState("conflict", err instanceof Error ? err.message : "Failed to sync draft");
      }
      return;
    }

    const baseRev = useEditorStore.getState().serverRevision;
    try {
      const updated = await api.putTimeline(projectId, baseRev, document);
      if (!mounted.current) return;
      markSyncedFromServer(updated);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setSyncState(
          "conflict",
          `Server has moved past draft base rev ${baseRev}. Click Reload Server to avoid overwriting newer changes.`,
        );
      } else {
        setSyncState("conflict", err instanceof Error ? err.message : "Failed to sync draft");
      }
    }
  }

  async function handleDiscardDraft() {
    if (drainingOpsRef.current || mediaBusyRef.current) return;
    try {
      const fresh = await api.getProject(projectId);
      if (!mounted.current) return;
      pendingOpsRef.current = [];
      mergeMediaUrls(fresh.data.media_urls);
      await discardLocalDraft(fresh.data.timeline);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Could not reload the project.");
    }
  }

  async function handleClearOpfsCache() {
    await clearOpfsProxyCache();
    clearOpfsCachedAssets();
    const stats = await getOpfsCacheStats();
    setOpfsStats(stats);
  }

  async function addScene(kind: "ai_video" | "diagram") {
    if (!document || mediaBusyRef.current) return;
    const id = `scn_${crypto.randomUUID()}`;
    const start = document.tracks.visual.at(-1)?.end_ms ?? 0;
    const command: TimelineCommand = kind === "diagram"
      ? { type: "insert_diagram", at_ms: start, duration_ms: 4000, scene_id: id,
          narration: "", template: "StatCallout", props: { title: "New diagram" } }
      : { type: "restore_scene", index: document.tracks.visual.length, scene: {
          id, start_ms: start, end_ms: start + 4000, narration: "", kind,
          model: "ltx25_fp8", camera: "slow_dolly_in", status: "planned" } };
    await commitTimelineCommands([command], kind === "diagram" ? "Add diagram scene" : "Add AI scene");
    selectScene(id);
    setInspector("Scene");
  }

  async function runMediaAction(action: (doc: TimelineDocument, revision: number) => Promise<{
    timeline?: TimelineDocument;
    urls?: Record<string, string>;
    restored?: boolean;
    message?: string;
  }>) {
    const state = useEditorStore.getState();
    if (!state.document || mediaBusyRef.current) return;
    if (drainingOpsRef.current || pendingOpsRef.current.length || (!isDemo && state.dirty)) {
      setNotice("Sync the current timeline edits before changing project media or restoring a revision.");
      return;
    }
    mediaBusyRef.current = true;
    setMediaBusy(true);
    setNotice("");
    try {
      const result = await action(state.document, state.serverRevision);
      if (!mounted.current) return;
      if (result.urls) mergeMediaUrls(result.urls);
      if (result.timeline) {
        if (result.restored) {
          await discardLocalDraft(result.timeline);
          if (!mounted.current) return;
          setHistoryOpen(false);
          setDraftReview("");
        }
        markSyncedFromServer(result.timeline);
      }
      if (result.message) setNotice(result.message);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "The project could not be updated.");
    } finally {
      mediaBusyRef.current = false;
      if (mounted.current) setMediaBusy(false);
    }
  }

  function changeMusic(source: File | MusicPreset | null) {
    void runMediaAction(async (doc, revision) => {
      const opts = { volume: doc.tracks.music.volume, duck_db: doc.tracks.music.duck_db };
      if (source === null) return { timeline: await api.removeMusic(projectId, revision) };
      if (source instanceof File && source.size > 25 * 1024 * 1024) {
        throw new Error("Music files must be at most 25 MiB.");
      }
      const result = typeof source === "string"
        ? await api.applyMusicPreset(projectId, source, opts, revision)
        : await api.uploadMusicFile(projectId, source, opts, revision);
      return { timeline: result.timeline, urls: { [result.asset_id]: result.media_url } };
    });
  }

  function changeVoice(voiceId: string) {
    void runMediaAction(async (doc, revision) => {
      const next = { ...doc, voice: { ...doc.voice, engine: "kokoro" as const, voice_id: voiceId } };
      return { timeline: await api.putTimeline(projectId, revision, next) };
    });
  }

  function revoice(sceneId: string) {
    void runMediaAction(async (_doc, revision) => {
      const queued = await api.revoiceScene(projectId, sceneId, revision);
      if (!queued.ok) return { message: "Connect a media worker to generate this voiceover." };
      const fresh = await api.getProject(projectId);
      return { timeline: fresh.data.timeline, urls: fresh.data.media_urls, message: "Voiceover and word alignment queued." };
    });
  }

  function restoreRevision(revision: number) {
    void runMediaAction(async (_doc, expected) => {
      const latest = await api.getProject(projectId);
      if (latest.data.timeline.revision !== expected) {
        reconcileServerSnapshot(latest.data.timeline);
        throw new Error("The timeline changed. Review its latest revision before restoring.");
      }
      const timeline = await api.restoreRevision(projectId, revision, expected);
      return { timeline, urls: latest.data.media_urls, restored: true, message: `Restored revision ${revision}.` };
    });
  }

  function scrubFromRulerPointer(clientX: number) {
    const rect = rulerRef.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0) return;
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    seekToMs(ratio * totalMs);
  }

  if (loadError) return <p role="alert" className="p-8 text-sm">{loadError}</p>;
  if (!document || document.project_id !== projectId) {
    return <div className="p-8 text-sm text-zinc-400">Loading browser timeline editor...</div>;
  }

  const autosaveBadgeText =
    autosaveStatus === "saving"
      ? "Saving..."
      : autosaveStatus === "saved"
        ? "Autosaved"
        : autosaveStatus === "restored"
          ? "Restored Draft"
          : autosaveStatus === "error"
            ? "Autosave Error"
            : "IDB Ready";

  const autosaveBadgeClass =
    autosaveStatus === "restored"
      ? "border-amber-500/40 bg-amber-500/15 text-amber-200"
      : autosaveStatus === "error"
        ? "border-rose-500/40 bg-rose-500/15 text-rose-200"
        : autosaveStatus === "saved"
          ? "border-emerald-500/30 bg-emerald-500/15 text-emerald-300"
          : "border-zinc-700 bg-zinc-900 text-zinc-300";

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
        ? "SSE Reconnecting"
        : sseStatus === "connecting"
          ? "SSE Connecting"
          : sseStatus === "demo"
            ? "Demo Mode"
            : "SSE Offline";

  const needsSpendConfirm = Boolean(
    pendingProposal && (pendingProposal.requires_confirmation || pendingProposal.est_cu > 1.0),
  );

  const playheadPct = Math.max(0, Math.min(100, (playheadMs / Math.max(1, totalMs)) * 100));
  const rulerSteps = Math.max(2, Math.min(12, Math.floor(totalMs / 5000) + 1));

  return (
    <div className="flex min-h-screen flex-col lg:h-screen bg-[#09090b]">
      {/* Top Toolbar */}
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800 bg-[#0d0e12] px-4 py-2.5">
        <div className="flex flex-wrap items-center gap-2.5">
          <Link href="/" className="text-xs text-zinc-400 hover:text-zinc-200">
            ← Library
          </Link>
          <span className="text-sm font-bold">Editor · rev {document.revision}</span>
          <span className="rounded bg-zinc-800 px-2 py-0.5 text-[11px]">
            {document.format.aspect}
          </span>
          <span className="rounded bg-zinc-800 px-2 py-0.5 text-[11px] text-amber-300 uppercase">
            {document.format.target}
          </span>

          {/* Crash-safe IndexedDB Autosave Status */}
          <span
            data-testid="autosave-status"
            className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${autosaveBadgeClass}`}
          >
            <Database className="h-3 w-3" />
            {autosaveBadgeText}
          </span>

          {/* Live SSE Status */}
          <span
            className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${sseBadgeClass}`}
          >
            <Radio className="h-3 w-3" />
            {sseLabel}
          </span>
          {(sseStatus === "reconnecting" || sseStatus === "closed") && !isDemo && (
            <button
              type="button"
              onClick={reconnectNow}
              className="rounded border border-zinc-700 bg-zinc-900 px-2 py-0.5 text-[11px] text-zinc-300 hover:bg-zinc-800"
            >
              Reconnect
            </button>
          )}

          {/* Sync / Conflict Controls */}
          {(syncStatus === "conflict" || (!isDemo && dirty && (syncStatus === "offline" || restoredFromDraft))) && (
            <div className="inline-flex items-center gap-1.5 rounded-full border border-rose-500/40 bg-rose-500/15 px-2.5 py-0.5 text-[11px] text-rose-200">
              <AlertTriangle className="h-3 w-3" />
              <span>{syncError ?? "Unsynced draft"}</span>
              <button
                type="button"
                onClick={handleSyncDraftToServer}
                className="rounded bg-rose-500 px-1.5 py-0.5 font-semibold text-zinc-950 hover:bg-rose-400"
              >
                Sync Draft
              </button>
            </div>
          )}

          {(restoredFromDraft || dirty) && (
            <button
              type="button"
              onClick={handleDiscardDraft}
              className="inline-flex items-center gap-1 rounded-full border border-zinc-700 bg-zinc-900 px-2.5 py-0.5 text-[11px] text-zinc-300 hover:border-rose-500/40 hover:text-rose-200"
              title="Discard offline IndexedDB draft and reload clean project"
            >
              <RotateCcw className="h-3 w-3" />
              Discard Draft
            </button>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button className={inspectorButton} onClick={() => { setNotice(""); setHistoryOpen(true); }}>Revision History</button>
          <button
            type="button"
            onClick={() => void handleUndo()}
            disabled={undoStack.length === 0}
            className="inline-flex items-center gap-1 rounded-lg border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-xs disabled:opacity-40 hover:bg-zinc-800"
            title="Undo (Ctrl+Z)"
          >
            <Undo2 className="h-3.5 w-3.5" />
            Undo{undoStack.length > 0 ? ` (${undoStack.length})` : ""}
          </button>
          <button
            type="button"
            onClick={() => void handleRedo()}
            disabled={redoStack.length === 0}
            className="inline-flex items-center gap-1 rounded-lg border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-xs disabled:opacity-40 hover:bg-zinc-800"
            title="Redo (Ctrl+Y / Ctrl+Shift+Z)"
          >
            <Redo2 className="h-3.5 w-3.5" />
            Redo{redoStack.length > 0 ? ` (${redoStack.length})` : ""}
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

      {notice && !historyOpen && <div role="status" className="flex items-center justify-between gap-3 bg-zinc-800 px-4 py-2 text-xs">
        {notice}<button onClick={() => setNotice("")} aria-label="Dismiss message"><X size={14} /></button>
      </div>}
      {historyOpen && <RevisionDrawer projectId={projectId} revision={document.revision} busy={mediaBusy}
        notice={notice} close={() => setHistoryOpen(false)} restore={restoreRevision} />}
      {/* Main 3-Pane Studio */}
      <div className="grid flex-1 min-h-0 grid-cols-1 lg:grid-cols-12">
        {/* Left: Scene List & Inspector */}
        <div className="lg:col-span-3 flex flex-col border-r border-zinc-800 bg-[#0d0e12] min-h-0 overflow-y-auto">
          <div className="flex items-center justify-between border-b border-zinc-800 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-zinc-400">
            <span>Scenes ({document.tracks.visual.length})</span>
            <span className="text-[10px] font-normal normal-case text-zinc-500">Drag to reorder</span>
          </div>
          <div className="flex flex-wrap gap-1.5 px-2.5 pt-2">
            <button className={inspectorButton} onClick={() => void addScene("diagram")}>+ Add Diagram Scene</button>
            <button className={inspectorButton} onClick={() => void addScene("ai_video")}>+ Add AI Scene</button>
          </div>
          <div className="space-y-1.5 p-2.5">
            {document.tracks.visual.map((s, idx) => {
              const liveProg = sceneProgress[s.id];
              const isDropTarget = dropTargetSceneId === s.id && draggedSceneId !== s.id;
              return (
                <div
                  key={s.id}
                  draggable={!trimDrag}
                  onDragStart={(e) => {
                    e.dataTransfer.setData("text/plain", s.id);
                    e.dataTransfer.effectAllowed = "move";
                    setDraggedSceneId(s.id);
                  }}
                  onDragOver={(e) => {
                    e.preventDefault();
                    setDropTargetSceneId(s.id);
                  }}
                  onDragLeave={() => {
                    if (dropTargetSceneId === s.id) setDropTargetSceneId(null);
                  }}
                  onDrop={(e) => {
                    e.preventDefault();
                    const sourceId = e.dataTransfer.getData("text/plain") || draggedSceneId;
                    setDraggedSceneId(null);
                    setDropTargetSceneId(null);
                    if (sourceId) void handleReorderScenes(sourceId, s.id);
                  }}
                  onDragEnd={() => {
                    setDraggedSceneId(null);
                    setDropTargetSceneId(null);
                  }}
                  onClick={() => {
                    selectScene(s.id);
                    seekToMs(s.start_ms);
                  }}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      selectScene(s.id);
                      seekToMs(s.start_ms);
                    }
                  }}
                  className={`w-full cursor-pointer rounded-lg border p-2.5 text-left transition ${
                    isDropTarget
                      ? "border-cyan-400 ring-1 ring-cyan-400/50 bg-cyan-500/10"
                      : selectedSceneId === s.id
                        ? "border-amber-500 bg-amber-500/10"
                        : "border-zinc-800 bg-zinc-900/50 hover:border-zinc-700"
                  }`}
                >
                  <div className="flex items-center justify-between gap-1.5">
                    <span className="inline-flex items-center gap-1 font-mono text-[11px] text-zinc-400">
                      <GripVertical className="h-3 w-3 text-zinc-600" />
                      #{String(idx + 1).padStart(2, "0")} · {((s.end_ms - s.start_ms) / 1000).toFixed(1)}s
                    </span>
                    <div className="flex items-center gap-1.5">
                      {s.qa?.score !== null && s.qa?.score !== undefined && (
                        <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] font-semibold text-cyan-300">
                          QA {s.qa.score.toFixed(1)}
                        </span>
                      )}
                      <StatusBadge value={s.status} />
                    </div>
                  </div>
                  <p className="mt-1 line-clamp-2 text-xs text-zinc-200">{s.narration}</p>
                  {liveProg && (
                    <div className="mt-1.5 space-y-0.5">
                      <div className="flex justify-between text-[10px] text-amber-300">
                        <span>{liveProg.jobType}</span>
                        <span>{liveProg.percent}%</span>
                      </div>
                      <div className="h-1 w-full overflow-hidden rounded-full bg-zinc-800">
                        <div
                          className="h-full rounded-full bg-amber-400 transition-all"
                          style={{ width: `${Math.max(4, liveProg.percent)}%` }}
                        />
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Center: @remotion/player Preview Stage */}
        <div className="lg:col-span-6 flex flex-col items-center justify-center bg-[#09090b] p-4 min-h-[300px] min-w-0">
          <div
            className="relative flex items-center justify-center overflow-hidden rounded-xl border border-zinc-800 bg-black shadow-2xl"
            style={{
              width: "100%",
              maxWidth: document.format.aspect === "9:16" ? 290 : 700,
              aspectRatio: document.format.aspect === "9:16" ? "9 / 16" : "16 / 9",
            }}
          >
            <Player
              ref={playerRef}
              component={EduComposition}
              inputProps={{ document, mediaUrls: effectiveMediaUrls }}
              durationInFrames={durationInFrames}
              compositionWidth={document.format.width}
              compositionHeight={document.format.height}
              fps={fps}
              controls
              spaceKeyToPlayOrPause={false}
              style={{ width: "100%", height: "100%" }}
            />
          </div>
          <div className="mt-2 text-[11px] text-zinc-500">
            Unified Remotion Engine — browser preview matches final {document.format.target.toUpperCase()} export 1:1
          </div>
        </div>

        {/* Right: AI Editing Copilot */}
        <div className="lg:col-span-3 flex flex-col overflow-y-auto border-l border-zinc-800 bg-[#0d0e12] min-h-0">
          <fieldset disabled={mediaBusy} className="shrink-0">
            <div role="tablist" aria-label="Inspector" className="flex flex-wrap gap-1 border-b border-zinc-800 p-2">
              {(["Scene", "Audio & Music", "Captions & Overlays"] as const).map((tab, index) => (
                <button key={tab} role="tab" id={`inspector-tab-${index}`} aria-controls="inspector-content" aria-selected={tab === inspector}
                  className={`${inspectorButton} aria-selected:border-amber-500 aria-selected:text-amber-200`} onClick={() => setInspector(tab)}>{tab}</button>
              ))}
            </div>
            <section role="tabpanel" id="inspector-content" aria-labelledby={`inspector-tab-${["Scene", "Audio & Music", "Captions & Overlays"].indexOf(inspector)}`}>
          {inspector === "Scene" && selectedScene && (
            <div className="mt-auto border-t border-zinc-800 p-3 space-y-2.5 bg-[#111216]">
              <div className="flex items-center justify-between">
                <div className="text-xs font-semibold text-amber-300">
                  Scene Inspector ({selectedScene.id})
                </div>
                <div className="font-mono text-[10px] text-zinc-400">
                  {(selectedScene.start_ms / 1000).toFixed(1)}s–{(selectedScene.end_ms / 1000).toFixed(1)}s
                </div>
              </div>

              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => void handleQuickTrimDelta(-500)}
                  disabled={selectedScene.end_ms - selectedScene.start_ms <= 1000}
                  className="flex-1 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-[11px] text-zinc-300 hover:bg-zinc-800 disabled:opacity-40"
                >
                  Trim -0.5s
                </button>
                <button
                  type="button"
                  onClick={() => void handleQuickTrimDelta(500)}
                  className="flex-1 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-[11px] text-zinc-300 hover:bg-zinc-800"
                >
                  Trim +0.5s
                </button>
              </div>

              <textarea
                rows={2}
                aria-label="Scene narration"
                value={narrationDraft}
                onChange={(e) => setNarrationDraft(e.target.value)}
                className="w-full rounded border border-zinc-700 bg-zinc-900 p-2 text-xs outline-none focus:border-amber-500"
                placeholder="English narration..."
              />

              {selectedScene.kind === "diagram" ? (
                <div className="space-y-1">
                  <label className="text-[11px] text-zinc-400">Diagram Template</label>
                  <select
                    aria-label="Diagram Template"
                    value={selectedScene.template ?? "BarCompare"}
                    onChange={(e) => {
                      const nextTpl = e.target.value as DiagramTemplate;
                      void commitTimelineCommands(
                        [
                          {
                            type: "convert_scene_to_diagram",
                            scene_id: selectedScene.id,
                            template: nextTpl,
                            props: selectedScene.props ?? {
                              title: "Visual Breakdown",
                              steps: [selectedScene.narration],
                            },
                          },
                        ],
                        `Template ${nextTpl}`,
                      );
                    }}
                    className="w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-[11px]"
                  >
                    <option value="BarCompare">BarCompare</option>
                    <option value="StepBreakdown">StepBreakdown</option>
                    <option value="TimelineFlow">TimelineFlow</option>
                    <option value="StatCallout">StatCallout</option>
                  </select>
                </div>
              ) : (
                <>
                  <textarea
                    rows={2}
                    aria-label="Visual prompt"
                    value={promptDraft}
                    onChange={(e) => setPromptDraft(e.target.value)}
                    className="w-full rounded border border-zinc-700 bg-zinc-900 p-2 text-xs outline-none focus:border-amber-500"
                    placeholder="Visual prompt..."
                  />
                  <div className="grid grid-cols-2 gap-1.5">
                    <select
                      aria-label="Scene model"
                      value={selectedScene.model ?? "ltx25_fp8"}
                      onChange={(e) =>
                        void triggerSceneRegeneration(
                          selectedScene.id,
                          { model: e.target.value as ModelChoice },
                          `Model ${e.target.value}`,
                        )
                      }
                      className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-[11px]"
                    >
                      <option value="ltx25_fp8">LTX-2.5 FP8</option>
                      <option value="wan22_i2v">Wan 2.2 Fast</option>
                      <option value="qwen_image">Qwen-Image</option>
                    </select>
                    <select
                      aria-label="Scene camera"
                      value={selectedScene.camera ?? "slow_dolly_in"}
                      onChange={(e) =>
                        void triggerSceneRegeneration(
                          selectedScene.id,
                          { camera: e.target.value as CameraPreset },
                          `Camera ${e.target.value}`,
                        )
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
                type="button"
                onClick={handleSaveSceneInspector}
                className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-zinc-800 py-1.5 text-xs font-semibold hover:bg-zinc-700"
              >
                <RefreshCw className="h-3.5 w-3.5 text-amber-400" />
                Apply Scene Edit
              </button>
              <div className="flex flex-wrap gap-1.5">
                <button className={inspectorButton} disabled={!selectedScene.narration.trim() || narrationDraft !== selectedScene.narration}
                  title="Save the narration before re-voicing" onClick={() => revoice(selectedScene.id)}>Re-Voice Scene</button>
                {selectedScene.kind !== "diagram" && <>
                  <button className={inspectorButton} onClick={() => void triggerSceneRegeneration(selectedScene.id, { prompt: promptDraft || undefined }, "Re-roll scene")}>Re-roll</button>
                  <button className={inspectorButton} onClick={() => void commitTimelineCommands([{ type: "convert_scene_to_diagram",
                    scene_id: selectedScene.id, template: "StepBreakdown", props: { title: "Scene breakdown", steps: [selectedScene.narration] } }])}>Convert to Diagram</button>
                </>}
                <button className={inspectorButton} onClick={() => void commitTimelineCommands([{ type: "delete_scene", scene_id: selectedScene.id }], "Delete scene")}>Delete Scene</button>
              </div>
            </div>
          )}
              {inspector === "Scene" && !selectedScene && <p className="p-3 text-xs">Add a scene to edit its narration and visuals.</p>}
              {inspector === "Audio & Music" && <AudioInspector doc={document} changeVoice={changeVoice} setMusic={changeMusic}
                removeMusic={() => changeMusic(null)} command={(cmd) => void commitTimelineCommands([cmd])} />}
              {inspector === "Captions & Overlays" && <CaptionInspector doc={document} scene={selectedScene}
                command={(cmd) => void commitTimelineCommands([cmd])} />}
            </section>
          </fieldset>
          <div className="flex items-center gap-2 border-b border-zinc-800 px-3.5 py-2.5 text-xs font-semibold text-amber-300">
            <Sparkles className="h-4 w-4" />
            AI Editing Copilot (Gemini + MCP)
          </div>

          <div className="space-y-3 p-3 text-xs">
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
                  type="button"
                  key={quick}
                  onClick={() => setChatInput(quick)}
                  className="rounded-full border border-zinc-700 bg-zinc-900 px-2.5 py-1 text-[11px] text-zinc-300 hover:border-amber-500/50"
                >
                  {quick}
                </button>
              ))}
            </div>

            <button className={inspectorButton} disabled={chatBusy || (!isDemo && dirty)} onClick={() => void askCopilot("review_draft")}>
              Review Whole Draft
            </button>
            {draftReview && <section aria-label="Draft review" className="space-y-2 rounded-lg border border-cyan-800 bg-cyan-950/20 p-3">
              {draftReview.split("\n").filter(Boolean).map((line, i) => {
                const match = /^(Pacing|Hook retention|Diagram density):\s*(.*)$/i.exec(line);
                return match ? <div key={i}><h3 className="font-semibold text-cyan-300">{match[1]}</h3><p>{match[2]}</p></div> : <p key={i}>{line}</p>;
              })}
            </section>}
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
                {needsSpendConfirm && (
                  <label className="flex items-center gap-2 rounded border border-amber-500/40 bg-black/30 px-2 py-1.5 text-[11px] text-amber-200">
                    <input
                      type="checkbox"
                      checked={confirmSpend}
                      onChange={(e) => setConfirmSpend(e.target.checked)}
                    />
                    Confirm GPU spend (&gt; 1.0 CU)
                  </label>
                )}
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={handleAcceptProposal}
                    disabled={mediaBusy || syncStatus === "syncing" || (needsSpendConfirm && !confirmSpend)}
                    className="inline-flex flex-1 items-center justify-center gap-1 rounded-lg bg-emerald-500 py-1.5 font-semibold text-zinc-950 hover:bg-emerald-400 disabled:opacity-40"
                  >
                    <Check className="h-3.5 w-3.5" />
                    Accept
                  </button>
                  <button
                    type="button"
                    onClick={() => setPendingProposal(null)}
                    className="inline-flex items-center justify-center gap-1 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-zinc-300 hover:bg-zinc-800"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            )}
          </div>

          <form onSubmit={(e) => { e.preventDefault(); void askCopilot(chatInput.trim()); }} className="border-t border-zinc-800 p-3 flex gap-2">
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

      {/* Bottom Interactive Multi-Track Timeline */}
      <div className="border-t border-zinc-800 bg-[#0d0e12] px-4 py-2.5 space-y-2">
        {/* Timeline Action Bar */}
        <div className="flex flex-wrap items-center justify-between gap-2 text-[11px]">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded border border-zinc-800 bg-zinc-950 px-2 py-1 font-mono text-amber-300">
              {formatTimeMs(playheadMs)} / {formatTimeMs(totalMs)}
            </span>
            <button
              type="button"
              data-testid="split-playhead-btn"
              onClick={() => void handleSplitAtPlayhead()}
              disabled={!canSplitAtPlayhead}
              className="inline-flex items-center gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/15 px-2.5 py-1 font-semibold text-amber-200 hover:bg-amber-500/25 disabled:opacity-40"
              title="Split scene at playhead (S)"
            >
              <Scissors className="h-3.5 w-3.5" />
              Split at Playhead
            </button>
            <span className="hidden sm:inline text-zinc-500">
              Drag clips to reorder · Drag edges to trim · Click ruler to scrub
            </span>
          </div>

          <div className="flex items-center gap-2">
            <span
              data-testid="opfs-status"
              className="inline-flex items-center gap-1.5 rounded-full border border-zinc-700 bg-zinc-900 px-2.5 py-0.5 text-[11px] text-zinc-300"
            >
              <HardDrive className="h-3 w-3 text-cyan-400" />
              {opfsStats?.supported
                ? opfsStats.entryCount > 0
                  ? `OPFS 540p Cache: ${opfsStats.entryCount} (${formatBytes(opfsStats.totalBytes)})`
                  : "OPFS Ready"
                : "OPFS Unavailable"}
            </span>
            {opfsStats && opfsStats.entryCount > 0 && (
              <button
                type="button"
                onClick={() => void handleClearOpfsCache()}
                className="inline-flex items-center gap-1 rounded border border-zinc-700 bg-zinc-900 px-2 py-0.5 text-[11px] text-zinc-400 hover:text-rose-300"
                title="Clear OPFS proxy cache"
              >
                <Trash2 className="h-3 w-3" />
                Clear
              </button>
            )}
          </div>
        </div>

        {/* Clickable / Scrubbable Time Ruler */}
        <div className="flex items-center gap-3 text-[10px]">
          <span className="w-16 shrink-0 font-mono text-zinc-500">TIME</span>
          <div
            ref={rulerRef}
            data-testid="timeline-ruler"
            onPointerDown={(e) => {
              scrubbingRulerRef.current = true;
              e.currentTarget.setPointerCapture(e.pointerId);
              scrubFromRulerPointer(e.clientX);
            }}
            onPointerMove={(e) => {
              if (scrubbingRulerRef.current) {
                scrubFromRulerPointer(e.clientX);
              }
            }}
            onPointerUp={(e) => {
              if (scrubbingRulerRef.current) {
                scrubbingRulerRef.current = false;
                if (e.currentTarget.hasPointerCapture(e.pointerId)) {
                  e.currentTarget.releasePointerCapture(e.pointerId);
                }
              }
            }}
            className="relative h-5 flex-1 cursor-ew-resize select-none rounded border border-zinc-800/80 bg-zinc-950"
          >
            {Array.from({ length: rulerSteps }).map((_, i) => {
              const stepMs = Math.round((totalMs / Math.max(1, rulerSteps - 1)) * i);
              const leftPct = (stepMs / Math.max(1, totalMs)) * 100;
              return (
                <div
                  key={i}
                  style={{ left: `${Math.min(96, leftPct)}%` }}
                  className="absolute top-0 bottom-0 flex items-center border-l border-zinc-800 pl-1 font-mono text-zinc-500"
                >
                  {(stepMs / 1000).toFixed(0)}s
                </div>
              );
            })}
            <div
              style={{ left: `${playheadPct}%` }}
              className="pointer-events-none absolute top-0 bottom-0 w-0.5 bg-amber-400 shadow-[0_0_8px_#f59e0b]"
            >
              <div className="absolute -top-0.5 -left-1.5 h-2.5 w-3.5 rounded-sm bg-amber-400" />
            </div>
          </div>
        </div>

        {/* Visual Track with Drag-to-Reorder & Edge-Trim Handles */}
        <div className="flex items-center gap-3 text-[11px]">
          <span className="w-16 shrink-0 font-semibold text-zinc-400">VISUALS</span>
          <div
            ref={visualTrackRef}
            className="relative flex flex-1 gap-1 overflow-x-auto py-0.5"
          >
            {document.tracks.visual.map((s) => {
              const isTrimmingThis = trimDrag?.sceneId === s.id;
              const effectiveStart = isTrimmingThis ? trimDrag.previewStartMs : s.start_ms;
              const effectiveEnd = isTrimmingThis ? trimDrag.previewEndMs : s.end_ms;
              const effectiveDurationMs = Math.max(500, effectiveEnd - effectiveStart);
              const pct = Math.max(8, (effectiveDurationMs / Math.max(1, totalMs)) * 100);
              const isDropTarget = dropTargetSceneId === s.id && draggedSceneId !== s.id;
              const liveProg = sceneProgress[s.id];

              return (
                <div
                  key={s.id}
                  data-testid={`timeline-clip-${s.id}`}
                  draggable={!trimDrag}
                  onDragStart={(e) => {
                    e.dataTransfer.setData("text/plain", s.id);
                    e.dataTransfer.effectAllowed = "move";
                    setDraggedSceneId(s.id);
                  }}
                  onDragOver={(e) => {
                    e.preventDefault();
                    setDropTargetSceneId(s.id);
                  }}
                  onDragLeave={() => {
                    if (dropTargetSceneId === s.id) setDropTargetSceneId(null);
                  }}
                  onDrop={(e) => {
                    e.preventDefault();
                    const sourceId = e.dataTransfer.getData("text/plain") || draggedSceneId;
                    setDraggedSceneId(null);
                    setDropTargetSceneId(null);
                    if (sourceId) void handleReorderScenes(sourceId, s.id);
                  }}
                  onDragEnd={() => {
                    setDraggedSceneId(null);
                    setDropTargetSceneId(null);
                  }}
                  onClick={() => {
                    selectScene(s.id);
                    seekToMs(s.start_ms);
                  }}
                  style={{ width: `${pct}%` }}
                  className={`group relative shrink-0 cursor-pointer select-none rounded border px-3 py-1.5 text-left text-[11px] font-medium transition ${
                    isDropTarget
                      ? "border-cyan-400 ring-2 ring-cyan-400/50 bg-cyan-500/20 text-cyan-100"
                      : selectedSceneId === s.id
                        ? "border-amber-400 bg-amber-500/25 text-amber-100"
                        : s.kind === "diagram"
                          ? "border-cyan-500/30 bg-cyan-500/15 text-cyan-200"
                          : "border-zinc-700 bg-zinc-800/80 text-zinc-200"
                  }`}
                >
                  {/* Left Trim Handle */}
                  <div
                    data-testid={`trim-start-${s.id}`}
                    onPointerDown={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      selectScene(s.id);
                      const trackWidth =
                        visualTrackRef.current?.getBoundingClientRect().width || 800;
                      setTrimDrag({
                        sceneId: s.id,
                        edge: "start",
                        startClientX: e.clientX,
                        origStartMs: s.start_ms,
                        origEndMs: s.end_ms,
                        previewStartMs: s.start_ms,
                        previewEndMs: s.end_ms,
                        msPerPx: totalMs / Math.max(200, trackWidth),
                      });
                    }}
                    title="Drag left edge to trim start"
                    className="absolute left-0 top-0 bottom-0 z-10 w-2 cursor-ew-resize rounded-l bg-amber-400/0 transition hover:bg-amber-400/70"
                  />

                  <div className="flex items-center justify-between gap-1 truncate">
                    <span className="inline-flex items-center gap-1 truncate">
                      <GripVertical className="h-3 w-3 shrink-0 opacity-60" />
                      {s.id} · {s.kind === "diagram" ? s.template : s.model ?? "ai"}
                    </span>
                    <span className="shrink-0 font-mono text-[10px] opacity-75">
                      {(effectiveDurationMs / 1000).toFixed(1)}s
                    </span>
                  </div>

                  {isTrimmingThis && (
                    <div className="pointer-events-none absolute -top-6 left-1/2 -translate-x-1/2 rounded bg-amber-400 px-1.5 py-0.5 font-mono text-[10px] font-bold text-zinc-950 shadow">
                      Trim: {(effectiveDurationMs / 1000).toFixed(1)}s
                    </div>
                  )}

                  {liveProg && (
                    <div className="absolute left-1 right-1 bottom-0.5 h-0.5 overflow-hidden rounded-full bg-black/40">
                      <div
                        className="h-full bg-amber-400"
                        style={{ width: `${Math.max(5, liveProg.percent)}%` }}
                      />
                    </div>
                  )}

                  {/* Right Trim Handle */}
                  <div
                    data-testid={`trim-end-${s.id}`}
                    onPointerDown={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      selectScene(s.id);
                      const trackWidth =
                        visualTrackRef.current?.getBoundingClientRect().width || 800;
                      setTrimDrag({
                        sceneId: s.id,
                        edge: "end",
                        startClientX: e.clientX,
                        origStartMs: s.start_ms,
                        origEndMs: s.end_ms,
                        previewStartMs: s.start_ms,
                        previewEndMs: s.end_ms,
                        msPerPx: totalMs / Math.max(200, trackWidth),
                      });
                    }}
                    title="Drag right edge to trim duration"
                    className="absolute right-0 top-0 bottom-0 z-10 w-2 cursor-ew-resize rounded-r bg-amber-400/0 transition hover:bg-amber-400/70"
                  />
                </div>
              );
            })}

            {/* Playhead Overlay Line */}
            <div
              style={{ left: `${playheadPct}%` }}
              className="pointer-events-none absolute top-0 bottom-0 z-20 w-0.5 bg-amber-400/80"
            />
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

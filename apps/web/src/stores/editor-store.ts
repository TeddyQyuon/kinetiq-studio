import { create } from "zustand";
import {
  applyCommandBatch,
  type CopilotProposal,
  type StudioEvent,
  type TimelineCommand,
  type TimelineDocument,
} from "@kinetiq/contracts";
import {
  clearEditorDraft,
  loadEditorDraft,
  saveEditorDraft,
  type EditorAutosaveRecord,
  type TimelineHistoryEntry,
} from "@/lib/idb-autosave";
import {
  applyStudioEventToProgress,
  applyStudioEventToTimeline,
  type SceneJobProgress,
} from "@/lib/live-events";

export type AutosaveStatus = "idle" | "saving" | "saved" | "restored" | "error";
export type ServerSyncStatus = "synced" | "dirty" | "syncing" | "conflict" | "offline";

const MAX_HISTORY = 50;
let autosaveSeq = 0;

function makeEntryId(): string {
  return `hist_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
}

function buildExactInverse(
  docBefore: TimelineDocument,
  commands: TimelineCommand[],
  fallbackInverses: TimelineCommand[],
): TimelineCommand[] {
  if (commands.length === 1 && commands[0].type === "split_scene") {
    const splitCmd = commands[0];
    const orig = docBefore.tracks.visual.find((s) => s.id === splitCmd.scene_id);
    if (orig) {
      return [
        { type: "delete_scene", scene_id: splitCmd.new_scene_id },
        {
          type: "trim_scene",
          scene_id: orig.id,
          start_ms: orig.start_ms,
          end_ms: orig.end_ms,
        },
        { type: "tighten_pauses", max_gap_ms: 0 },
      ];
    }
  }

  if (
    commands.length === 2 &&
    commands[0].type === "trim_scene" &&
    commands[1].type === "tighten_pauses"
  ) {
    const trimCmd = commands[0];
    const orig = docBefore.tracks.visual.find((s) => s.id === trimCmd.scene_id);
    if (orig) {
      return [
        {
          type: "trim_scene",
          scene_id: orig.id,
          start_ms: orig.start_ms,
          end_ms: orig.end_ms,
        },
        { type: "tighten_pauses", max_gap_ms: 0 },
      ];
    }
  }

  return fallbackInverses;
}

interface EditorState {
  projectId: string | null;
  document: TimelineDocument | null;
  serverRevision: number;
  mediaUrls: Record<string, string>;
  opfsCachedAssets: Record<string, string>;
  selectedSceneId: string | null;
  undoStack: TimelineHistoryEntry[];
  redoStack: TimelineHistoryEntry[];
  pendingProposal: CopilotProposal | null;
  isDemo: boolean;
  dirty: boolean;
  autosaveStatus: AutosaveStatus;
  lastSavedAt: number | null;
  restoredFromDraft: boolean;
  syncStatus: ServerSyncStatus;
  syncError: string | null;
  sceneProgress: Record<string, SceneJobProgress>;

  hydrate: (
    projectId: string,
    doc: TimelineDocument,
    mediaUrls: Record<string, string>,
    isDemo: boolean,
  ) => void;
  hydrateWithAutosave: (
    projectId: string,
    serverDoc: TimelineDocument,
    mediaUrls: Record<string, string>,
    isDemo: boolean,
  ) => Promise<void>;
  selectScene: (sceneId: string | null) => void;
  dispatchLocalCommand: (cmd: TimelineCommand, label?: string) => TimelineDocument | null;
  dispatchLocalBatch: (commands: TimelineCommand[], label?: string) => TimelineDocument | null;
  setPendingProposal: (proposal: CopilotProposal | null) => void;
  acceptPendingProposalLocal: () => TimelineDocument | null;
  undo: () => TimelineCommand[] | null;
  redo: () => TimelineCommand[] | null;
  markSyncedFromServer: (serverDoc: TimelineDocument) => void;
  setSyncState: (syncStatus: ServerSyncStatus, syncError?: string | null) => void;
  discardLocalDraft: (freshServerDoc: TimelineDocument) => Promise<void>;
  applyLiveEvent: (event: StudioEvent) => void;
  mergeMediaUrls: (urls: Record<string, string>) => void;
  setOpfsCachedAsset: (assetId: string, blobUrl: string) => void;
  clearOpfsCachedAssets: () => void;
}

function persistCurrentDraft(
  get: () => EditorState,
  set: (partial: Partial<EditorState>) => void,
): void {
  const state = get();
  if (!state.projectId || !state.document) return;

  const seq = ++autosaveSeq;
  const updatedAt = Date.now();
  const record: EditorAutosaveRecord = {
    projectId: state.projectId,
    document: state.document,
    serverRevision: state.serverRevision,
    undoStack: state.undoStack,
    redoStack: state.redoStack,
    selectedSceneId: state.selectedSceneId,
    dirty: state.dirty,
    updatedAt,
  };

  set({ autosaveStatus: "saving" });
  void saveEditorDraft(record).then((ok) => {
    if (seq !== autosaveSeq) return;
    set({
      autosaveStatus: ok ? "saved" : "error",
      lastSavedAt: ok ? updatedAt : get().lastSavedAt,
    });
  });
}

function resolveSelectedScene(
  doc: TimelineDocument,
  preferredId: string | null,
): string | null {
  if (preferredId && doc.tracks.visual.some((s) => s.id === preferredId)) {
    return preferredId;
  }
  return doc.tracks.visual[0]?.id ?? null;
}

export const useEditorStore = create<EditorState>((set, get) => ({
  projectId: null,
  document: null,
  serverRevision: 1,
  mediaUrls: {},
  opfsCachedAssets: {},
  selectedSceneId: null,
  undoStack: [],
  redoStack: [],
  pendingProposal: null,
  isDemo: false,
  dirty: false,
  autosaveStatus: "idle",
  lastSavedAt: null,
  restoredFromDraft: false,
  syncStatus: "synced",
  syncError: null,
  sceneProgress: {},

  hydrate: (projectId, doc, mediaUrls, isDemo) => {
    const switchingProject = get().projectId !== projectId;
    set({
      projectId,
      document: doc,
      serverRevision: doc.revision,
      mediaUrls,
      opfsCachedAssets: switchingProject ? {} : get().opfsCachedAssets,
      selectedSceneId: resolveSelectedScene(
        doc,
        switchingProject ? null : get().selectedSceneId,
      ),
      undoStack: [],
      redoStack: [],
      pendingProposal: null,
      isDemo,
      dirty: false,
      autosaveStatus: "idle",
      lastSavedAt: null,
      restoredFromDraft: false,
      syncStatus: isDemo ? "offline" : "synced",
      syncError: null,
      sceneProgress: switchingProject ? {} : get().sceneProgress,
    });
  },

  hydrateWithAutosave: async (projectId, serverDoc, mediaUrls, isDemo) => {
    const switchingProject = get().projectId !== projectId;
    const draft = await loadEditorDraft(projectId);

    const hasMeaningfulDraft =
      draft !== null &&
      draft.projectId === projectId &&
      (draft.dirty || draft.undoStack.length > 0 || draft.redoStack.length > 0);

    if (hasMeaningfulDraft) {
      if (isDemo) {
        set({
          projectId,
          document: draft.document,
          serverRevision: draft.serverRevision,
          mediaUrls,
          opfsCachedAssets: switchingProject ? {} : get().opfsCachedAssets,
          selectedSceneId: resolveSelectedScene(draft.document, draft.selectedSceneId),
          undoStack: draft.undoStack.slice(-MAX_HISTORY),
          redoStack: draft.redoStack.slice(-MAX_HISTORY),
          pendingProposal: null,
          isDemo: true,
          dirty: draft.dirty,
          autosaveStatus: "restored",
          lastSavedAt: draft.updatedAt,
          restoredFromDraft: true,
          syncStatus: "offline",
          syncError: null,
          sceneProgress: switchingProject ? {} : get().sceneProgress,
        });
        return;
      }

      if (draft.serverRevision === serverDoc.revision) {
        set({
          projectId,
          document: draft.document,
          serverRevision: serverDoc.revision,
          mediaUrls,
          opfsCachedAssets: switchingProject ? {} : get().opfsCachedAssets,
          selectedSceneId: resolveSelectedScene(draft.document, draft.selectedSceneId),
          undoStack: draft.undoStack.slice(-MAX_HISTORY),
          redoStack: draft.redoStack.slice(-MAX_HISTORY),
          pendingProposal: null,
          isDemo: false,
          dirty: draft.dirty,
          autosaveStatus: "restored",
          lastSavedAt: draft.updatedAt,
          restoredFromDraft: true,
          syncStatus: draft.dirty ? "dirty" : "synced",
          syncError: null,
          sceneProgress: switchingProject ? {} : get().sceneProgress,
        });
        return;
      }

      if (draft.dirty) {
        set({
          projectId,
          document: draft.document,
          serverRevision: serverDoc.revision,
          mediaUrls,
          opfsCachedAssets: switchingProject ? {} : get().opfsCachedAssets,
          selectedSceneId: resolveSelectedScene(draft.document, draft.selectedSceneId),
          undoStack: draft.undoStack.slice(-MAX_HISTORY),
          redoStack: draft.redoStack.slice(-MAX_HISTORY),
          pendingProposal: null,
          isDemo: false,
          dirty: true,
          autosaveStatus: "restored",
          lastSavedAt: draft.updatedAt,
          restoredFromDraft: true,
          syncStatus: "conflict",
          syncError: `Server is at rev ${serverDoc.revision}; local draft was based on rev ${draft.serverRevision}.`,
          sceneProgress: switchingProject ? {} : get().sceneProgress,
        });
        return;
      }

      await clearEditorDraft(projectId);
    }

    get().hydrate(projectId, serverDoc, mediaUrls, isDemo);
  },

  selectScene: (sceneId) => {
    set({ selectedSceneId: sceneId });
  },

  dispatchLocalCommand: (cmd, label) => {
    return get().dispatchLocalBatch([cmd], label ?? cmd.type);
  },

  dispatchLocalBatch: (commands, label) => {
    const doc = get().document;
    if (!doc || commands.length === 0) return null;

    const res = applyCommandBatch(doc, commands);
    const exactInverse = buildExactInverse(doc, commands, res.inverses);
    const entry: TimelineHistoryEntry = {
      id: makeEntryId(),
      label: label ?? (commands.length === 1 ? commands[0].type : `${commands.length} edits`),
      forward: commands,
      inverse: exactInverse,
      timestamp: Date.now(),
    };

    set((state) => ({
      document: res.doc,
      selectedSceneId: resolveSelectedScene(res.doc, state.selectedSceneId),
      undoStack: [...state.undoStack, entry].slice(-MAX_HISTORY),
      redoStack: [],
      dirty: true,
      syncStatus: state.isDemo ? "offline" : "dirty",
      syncError: null,
    }));

    persistCurrentDraft(get, set);
    return res.doc;
  },

  setPendingProposal: (proposal) => set({ pendingProposal: proposal }),

  acceptPendingProposalLocal: () => {
    const { document, pendingProposal } = get();
    if (!document || !pendingProposal) return null;
    const nextDoc = get().dispatchLocalBatch(pendingProposal.commands, "Copilot proposal");
    set({ pendingProposal: null });
    return nextDoc;
  },

  undo: () => {
    const { document, undoStack, redoStack, isDemo } = get();
    if (!document || undoStack.length === 0) return null;

    const entry = undoStack[undoStack.length - 1];
    const res = applyCommandBatch(document, entry.inverse);

    set((state) => ({
      document: res.doc,
      selectedSceneId: resolveSelectedScene(res.doc, state.selectedSceneId),
      undoStack: undoStack.slice(0, -1),
      redoStack: [...redoStack, entry].slice(-MAX_HISTORY),
      dirty: true,
      syncStatus: isDemo ? "offline" : "dirty",
      syncError: null,
    }));

    persistCurrentDraft(get, set);
    return entry.inverse;
  },

  redo: () => {
    const { document, undoStack, redoStack, isDemo } = get();
    if (!document || redoStack.length === 0) return null;

    const entry = redoStack[redoStack.length - 1];
    const res = applyCommandBatch(document, entry.forward);

    set((state) => ({
      document: res.doc,
      selectedSceneId: resolveSelectedScene(res.doc, state.selectedSceneId),
      redoStack: redoStack.slice(0, -1),
      undoStack: [...undoStack, entry].slice(-MAX_HISTORY),
      dirty: true,
      syncStatus: isDemo ? "offline" : "dirty",
      syncError: null,
    }));

    persistCurrentDraft(get, set);
    return entry.forward;
  },

  markSyncedFromServer: (serverDoc) => {
    set((state) => ({
      document: serverDoc,
      serverRevision: serverDoc.revision,
      selectedSceneId: resolveSelectedScene(serverDoc, state.selectedSceneId),
      dirty: false,
      syncStatus: "synced",
      syncError: null,
    }));
    persistCurrentDraft(get, set);
  },

  setSyncState: (syncStatus, syncError = null) => {
    set({ syncStatus, syncError });
  },

  discardLocalDraft: async (freshServerDoc) => {
    const { projectId, mediaUrls, isDemo } = get();
    if (projectId) {
      await clearEditorDraft(projectId);
    }
    if (projectId) {
      get().hydrate(projectId, freshServerDoc, mediaUrls, isDemo);
    }
  },

  applyLiveEvent: (event) => {
    set((state) => {
      const nextProgress = applyStudioEventToProgress(state.sceneProgress, event);
      let nextDoc = state.document;
      if (state.document) {
        const applied = applyStudioEventToTimeline(state.document, event);
        if (applied.changed) {
          nextDoc = applied.doc;
        }
      }
      return {
        document: nextDoc,
        sceneProgress: nextProgress,
      };
    });
  },

  mergeMediaUrls: (urls) => {
    set((state) => ({
      mediaUrls: { ...state.mediaUrls, ...urls },
    }));
  },

  setOpfsCachedAsset: (assetId, blobUrl) => {
    set((state) => ({
      opfsCachedAssets: { ...state.opfsCachedAssets, [assetId]: blobUrl },
    }));
  },

  clearOpfsCachedAssets: () => {
    set({ opfsCachedAssets: {} });
  },
}));

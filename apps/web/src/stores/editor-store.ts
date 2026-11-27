import { create } from "zustand";
import {
  applyCommandBatch,
  applyTimelineCommand,
  type CopilotProposal,
  type TimelineCommand,
  type TimelineDocument,
} from "@kinetiq/contracts";

interface EditorState {
  projectId: string | null;
  document: TimelineDocument | null;
  mediaUrls: Record<string, string>;
  selectedSceneId: string | null;
  undoStack: TimelineCommand[][];
  redoStack: TimelineCommand[][];
  pendingProposal: CopilotProposal | null;
  isDemo: boolean;

  hydrate: (
    projectId: string,
    doc: TimelineDocument,
    mediaUrls: Record<string, string>,
    isDemo: boolean,
  ) => void;
  selectScene: (sceneId: string | null) => void;
  dispatchLocalCommand: (cmd: TimelineCommand) => TimelineDocument | null;
  setPendingProposal: (proposal: CopilotProposal | null) => void;
  acceptPendingProposalLocal: () => void;
  undo: () => TimelineCommand[] | null;
  redo: () => TimelineCommand[] | null;
}

export const useEditorStore = create<EditorState>((set, get) => ({
  projectId: null,
  document: null,
  mediaUrls: {},
  selectedSceneId: null,
  undoStack: [],
  redoStack: [],
  pendingProposal: null,
  isDemo: false,

  hydrate: (projectId, doc, mediaUrls, isDemo) =>
    set({
      projectId,
      document: doc,
      mediaUrls,
      isDemo,
      selectedSceneId: get().selectedSceneId ?? doc.tracks.visual[0]?.id ?? null,
    }),

  selectScene: (sceneId) => set({ selectedSceneId: sceneId }),

  dispatchLocalCommand: (cmd) => {
    const doc = get().document;
    if (!doc) return null;
    const res = applyTimelineCommand(doc, cmd);
    set((state) => ({
      document: res.doc,
      undoStack: [...state.undoStack, [res.inverse]],
      redoStack: [],
    }));
    return res.doc;
  },

  setPendingProposal: (proposal) => set({ pendingProposal: proposal }),

  acceptPendingProposalLocal: () => {
    const { document, pendingProposal } = get();
    if (!document || !pendingProposal) return;
    const res = applyCommandBatch(document, pendingProposal.commands);
    set((state) => ({
      document: res.doc,
      undoStack: [...state.undoStack, res.inverses],
      redoStack: [],
      pendingProposal: null,
    }));
  },

  undo: () => {
    const { document, undoStack, redoStack } = get();
    if (!document || undoStack.length === 0) return null;
    const batch = undoStack[undoStack.length - 1];
    const res = applyCommandBatch(document, batch);
    set({
      document: res.doc,
      undoStack: undoStack.slice(0, -1),
      redoStack: [...redoStack, res.inverses],
    });
    return batch;
  },

  redo: () => {
    const { document, undoStack, redoStack } = get();
    if (!document || redoStack.length === 0) return null;
    const batch = redoStack[redoStack.length - 1];
    const res = applyCommandBatch(document, batch);
    set({
      document: res.doc,
      redoStack: redoStack.slice(0, -1),
      undoStack: [...undoStack, res.inverses],
    });
    return batch;
  },
}));

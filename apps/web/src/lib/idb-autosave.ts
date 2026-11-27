import type { TimelineCommand, TimelineDocument } from "@kinetiq/contracts";

const DB_NAME = "kinetiq_studio_autosave";
const DB_VERSION = 1;
const STORE_NAME = "editor_drafts";

export interface TimelineHistoryEntry {
  id: string;
  label: string;
  forward: TimelineCommand[];
  inverse: TimelineCommand[];
  timestamp: number;
}

export interface EditorAutosaveRecord {
  projectId: string;
  document: TimelineDocument;
  serverRevision: number;
  undoStack: TimelineHistoryEntry[];
  redoStack: TimelineHistoryEntry[];
  selectedSceneId: string | null;
  dirty: boolean;
  updatedAt: number;
}

let openDbPromise: Promise<IDBDatabase | null> | null = null;

export function isIndexedDBAvailable(): boolean {
  return typeof window !== "undefined" && typeof window.indexedDB !== "undefined";
}

function getDb(): Promise<IDBDatabase | null> {
  if (!isIndexedDBAvailable()) {
    return Promise.resolve(null);
  }
  if (openDbPromise) {
    return openDbPromise;
  }

  openDbPromise = new Promise<IDBDatabase | null>((resolve) => {
    try {
      const request = window.indexedDB.open(DB_NAME, DB_VERSION);

      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          db.createObjectStore(STORE_NAME, { keyPath: "projectId" });
        }
      };

      request.onsuccess = () => {
        const db = request.result;
        db.onclose = () => {
          openDbPromise = null;
        };
        db.onversionchange = () => {
          db.close();
          openDbPromise = null;
        };
        resolve(db);
      };

      request.onerror = () => {
        openDbPromise = null;
        resolve(null);
      };

      request.onblocked = () => {
        openDbPromise = null;
        resolve(null);
      };
    } catch {
      openDbPromise = null;
      resolve(null);
    }
  });

  return openDbPromise;
}

function isValidDraftRecord(raw: unknown): raw is EditorAutosaveRecord {
  if (!raw || typeof raw !== "object") return false;
  const r = raw as Partial<EditorAutosaveRecord>;
  return (
    typeof r.projectId === "string" &&
    Boolean(r.document) &&
    typeof r.document === "object" &&
    Boolean(r.document?.tracks) &&
    Array.isArray(r.document?.tracks?.visual) &&
    Array.isArray(r.undoStack) &&
    Array.isArray(r.redoStack)
  );
}

export async function saveEditorDraft(record: EditorAutosaveRecord): Promise<boolean> {
  const db = await getDb();
  if (!db) return false;

  return new Promise<boolean>((resolve) => {
    try {
      const tx = db.transaction(STORE_NAME, "readwrite");
      const store = tx.objectStore(STORE_NAME);
      const clone = JSON.parse(JSON.stringify(record)) as EditorAutosaveRecord;
      store.put(clone);

      tx.oncomplete = () => resolve(true);
      tx.onerror = () => resolve(false);
      tx.onabort = () => resolve(false);
    } catch {
      resolve(false);
    }
  });
}

export async function loadEditorDraft(projectId: string): Promise<EditorAutosaveRecord | null> {
  const db = await getDb();
  if (!db) return null;

  return new Promise<EditorAutosaveRecord | null>((resolve) => {
    try {
      const tx = db.transaction(STORE_NAME, "readonly");
      const store = tx.objectStore(STORE_NAME);
      const req = store.get(projectId);

      req.onsuccess = () => {
        const result = req.result as unknown;
        resolve(isValidDraftRecord(result) ? result : null);
      };
      req.onerror = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

export async function clearEditorDraft(projectId: string): Promise<void> {
  const db = await getDb();
  if (!db) return;

  await new Promise<void>((resolve) => {
    try {
      const tx = db.transaction(STORE_NAME, "readwrite");
      const store = tx.objectStore(STORE_NAME);
      store.delete(projectId);
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
      tx.onabort = () => resolve();
    } catch {
      resolve();
    }
  });
}

export async function listEditorDrafts(): Promise<EditorAutosaveRecord[]> {
  const db = await getDb();
  if (!db) return [];

  return new Promise<EditorAutosaveRecord[]>((resolve) => {
    try {
      const tx = db.transaction(STORE_NAME, "readonly");
      const store = tx.objectStore(STORE_NAME);
      const req = store.getAll();
      req.onsuccess = () => {
        const rows = Array.isArray(req.result) ? req.result : [];
        resolve(rows.filter(isValidDraftRecord));
      };
      req.onerror = () => resolve([]);
    } catch {
      resolve([]);
    }
  });
}
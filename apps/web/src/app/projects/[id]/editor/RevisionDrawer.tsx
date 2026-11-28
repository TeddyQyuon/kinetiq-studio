import { useEffect, useRef, useState } from "react";
import type { RevisionSummary } from "@kinetiq/contracts";
import { X } from "lucide-react";
import { api } from "@/lib/api-client";
import { inspectorButton } from "./TrackInspector";

export function RevisionDrawer({ projectId, revision, busy, notice, close, restore }: {
  projectId: string;
  revision: number;
  busy: boolean;
  notice: string;
  close: () => void;
  restore: (revision: number) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [rows, setRows] = useState<RevisionSummary[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  useEffect(() => {
    let active = true;
    setError("");
    api.listRevisions(projectId).then((revisions) => {
      if (active) setRows(revisions);
    }).catch((e: unknown) => {
      if (active) setError(e instanceof Error ? e.message : "Could not load revisions.");
    });
    return () => { active = false; };
  }, [projectId, revision]);

  return (
    <dialog ref={ref} aria-labelledby="revision-heading" onCancel={close}
      className="fixed inset-y-0 left-auto right-0 m-0 h-dvh max-h-none w-96 max-w-full border-l border-zinc-700 bg-zinc-950 p-5 text-zinc-100 backdrop:bg-black/60">
      <div className="mb-5 flex items-center justify-between">
        <h2 id="revision-heading" className="font-semibold">Revision History</h2>
        <button className={inspectorButton} autoFocus onClick={close} aria-label="Close history"><X size={16} /></button>
      </div>
      {notice && <p role="status" className="mb-3 text-amber-200">{notice}</p>}
      {error && <p role="alert">{error}</p>}
      {!rows && !error && <p role="status">Loading revisions…</p>}
      {rows?.length === 0 && <p>No saved revisions yet.</p>}
      <ol className="space-y-3">{rows?.map((row) => (
        <li key={row.revision} className="space-y-2 rounded border border-zinc-800 p-3 text-xs">
          <div className="flex justify-between"><strong>Revision {row.revision}</strong>
            <span className={row.drive_snapshot_id ? "text-emerald-300" : "text-zinc-400"}>
              {row.drive_snapshot_id ? "Vault backup" : "Awaiting vault backup"}
            </span>
          </div>
          <p>{row.author} · {row.commands_count} edits · <time dateTime={row.created_at}>{new Date(row.created_at).toLocaleString()}</time></p>
          <button disabled={busy || row.revision === revision} onClick={() => restore(row.revision)} className={inspectorButton}>
            {row.revision === revision ? "Current revision" : "Restore Revision"}
          </button>
        </li>
      ))}</ol>
    </dialog>
  );
}

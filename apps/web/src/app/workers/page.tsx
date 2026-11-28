"use client";

import React, { useEffect, useRef, useState } from "react";
import { Check, ClipboardCopy, Cpu, KeyRound, Terminal } from "lucide-react";
import type { HealthMetricsResponse, WorkerRecord } from "@kinetiq/contracts";
import { api } from "@/lib/api-client";
import { StatusBadge } from "@/components/StatusBadge";

export default function WorkersPage() {
  const [workers, setWorkers] = useState<WorkerRecord[]>([]);
  const [isDemo, setIsDemo] = useState(false);
  const [name, setName] = useState("colab-l4-main");
  const [profile, setProfile] = useState("ltx25_pro");
  const [issuedToken, setIssuedToken] = useState<{ worker_id: string; worker_token: string } | null>(null);
  const [copied, setCopied] = useState(false);

  const [metrics, setMetrics] = useState<HealthMetricsResponse | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const latestLoad = useRef(0);

  async function load() {
    const request = ++latestLoad.current;
    try {
      const [res, health] = await Promise.all([api.listWorkers(), api.getHealthMetrics().catch(() => null)]);
      if (request !== latestLoad.current) return;
      setWorkers(res.data);
      setIsDemo(res.demo);
      setMetrics(health);
    } catch (error) {
      if (request === latestLoad.current) setMessage(error instanceof Error ? error.message : "Worker data unavailable.");
    }
  }

  useEffect(() => {
    void load();
    return () => { ++latestLoad.current; };
  }, []);

  async function handleGenerateToken(e: React.FormEvent) {
    e.preventDefault();
    if (isDemo) {
      setIssuedToken({ worker_id: "", worker_token: "wrk_demo_start_fastapi_to_issue_real_tokens" });
      return;
    }
    setBusy(true);
    setIssuedToken(null);
    setMessage("");
    try {
      const res = await api.createWorkerToken(name.trim(), profile);
      setIssuedToken(res);
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not issue worker token.");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(worker: WorkerRecord) {
    if (!window.confirm(`Revoke the token for ${worker.name}?`)) return;
    setBusy(true);
    try {
      await api.revokeWorker(worker.id);
      setIssuedToken((current) => current?.worker_id === worker.id ? null : current);
      setMessage(`Revoked access for ${worker.name}.`);
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not revoke this worker.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6 md:p-8">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Google Colab GPU Workers &amp; Sync Hub</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Workers pull jobs over outbound HTTPS — no ngrok inbound ports, 15-min idle auto-shutdown protects your 200 CU/mo.
        </p>
      </div>

      {message && <p role="status" className="rounded-lg border border-amber-500/40 px-4 py-2 text-sm text-amber-200">{message}</p>}
      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold">System Health &amp; Telemetry</h2>
          <button className="rounded border border-zinc-700 px-3 py-1 text-xs" onClick={load} disabled={busy}>Refresh metrics</button>
        </div>
        {metrics ? <>
          <div className="flex flex-wrap gap-2 text-xs">
            <span className="rounded-full bg-zinc-800 px-2.5 py-1">{isDemo ? "Demo metrics" : metrics.env} · {metrics.status}</span>
            <span className={`rounded-full px-2.5 py-1 ${metrics.redis_connected ? "bg-emerald-500/15 text-emerald-300" : "bg-zinc-800 text-zinc-300"}`}>
              Redis Pub/Sub · {metrics.redis_connected ? "Live" : "Offline"}
            </span>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="space-y-1 rounded-lg border border-zinc-800 bg-[#111216] p-4 text-xs">
              <h3 className="mb-2 text-sm font-semibold">Job queue</h3>
              <p>{metrics.jobs.queued} queued · {metrics.jobs.leased} leased · {metrics.jobs.running} running</p>
              <p className="text-zinc-400">{metrics.jobs.succeeded} succeeded · {metrics.jobs.failed} failed</p>
            </div>
            <div className="rounded-lg border border-zinc-800 bg-[#111216] p-4">
              <h3 className="text-sm font-semibold">Dead-letter queue</h3>
              <p className={`mt-1 text-xl font-semibold ${metrics.jobs.dead ? "text-rose-300" : "text-zinc-300"}`}>{metrics.jobs.dead}</p>
            </div>
            <div className="space-y-1 rounded-lg border border-zinc-800 bg-[#111216] p-4 text-xs">
              <h3 className="mb-2 text-sm font-semibold">Vault sync · {metrics.storage_provider}</h3>
              <p>{metrics.vault.synced} synced · {metrics.vault.pending} pending</p>
              <p className="text-zinc-400">{metrics.vault.missing} missing · {metrics.vault.mismatch} mismatched</p>
            </div>
            <div className="rounded-lg border border-zinc-800 bg-[#111216] p-4 text-xs">
              <h3 className="mb-2 text-sm font-semibold">Snapshot coverage</h3>
              <p>{metrics.snapshots.backed_up_revisions} / {metrics.snapshots.total_revisions} revisions backed up</p>
            </div>
          </div>
        </> : <p className="text-xs text-zinc-400">System metrics unavailable.</p>}
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <form onSubmit={handleGenerateToken} className="rounded-xl border border-zinc-800 bg-[#111216] p-5 space-y-4">
          <div className="flex items-center gap-2 text-sm font-bold text-amber-300">
            <KeyRound className="h-4 w-4" />
            Issue a Worker Token
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="worker-name" className="text-xs text-zinc-400">Worker Name</label>
              <input
                id="worker-name" required value={name}
                onChange={(e) => setName(e.target.value)}
                className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-xs"
              />
            </div>
            <div>
              <label htmlFor="model-profile" className="text-xs text-zinc-400">Model Profile</label>
              <select
                id="model-profile" value={profile}
                onChange={(e) => setProfile(e.target.value)}
                className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-xs"
              >
                <option value="ltx25_pro">ltx25_pro (Qwen + LTX-2.5 1080p)</option>
                <option value="wan22_fast">wan22_fast (Qwen + Wan 2.2 4-step)</option>
                <option value="media_local">media_local (TTS, alignment &amp; transcode)</option>
                <option value="render_local">render_local (Remotion renderer)</option>
                <option value="upscale_4k">upscale_4k (FlashVSR v1.1 4K)</option>
              </select>
            </div>
          </div>
          <button
            type="submit" disabled={busy || !name.trim()}
            className="rounded-lg bg-amber-500 px-4 py-2 text-xs font-semibold text-zinc-950 hover:bg-amber-400"
          >
            Generate Scoped Worker Token
          </button>

          {issuedToken && (
            <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-xs space-y-1.5">
              <div className="font-semibold text-emerald-300">
                {isDemo ? "Example token. Start FastAPI to issue a worker token." : "Save this worker’s token as WORKER_TOKEN."}
              </div>
              <div className="flex items-center justify-between gap-2 font-mono text-[11px] bg-black/40 p-2 rounded">
                <span className="truncate">{issuedToken.worker_token}</span>
                <button
                  type="button"
                  aria-label="Copy worker token"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(issuedToken.worker_token);
                      setCopied(true);
                      setTimeout(() => setCopied(false), 1500);
                    } catch {
                      setMessage("Copy is unavailable. Select the token to copy it manually.");
                    }
                  }}
                >
                  {copied ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <ClipboardCopy className="h-3.5 w-3.5" />}
                </button>
              </div>
            </div>
          )}
        </form>

        <div className="rounded-xl border border-zinc-800 bg-[#111216] p-5 space-y-3 text-xs">
          <div className="flex items-center gap-2 text-sm font-bold text-cyan-300">
            <Terminal className="h-4 w-4" />
            Phase 0 Laptop Tunnel Command
          </div>
          <p className="text-zinc-400">
            Expose your local FastAPI server over HTTPS so Google Colab can claim jobs and stream Drive uploads:
          </p>
          <pre className="rounded-lg border border-zinc-800 bg-black/60 p-3 font-mono text-[11px] text-zinc-200 overflow-x-auto">
            cloudflared tunnel --url http://localhost:8000
          </pre>
          <p className="text-zinc-400">
            Put the generated <code className="text-zinc-200">https://*.trycloudflare.com</code> URL into Colab Secrets as{" "}
            <code className="text-amber-300">API_BASE_URL</code>, then run{" "}
            <code className="text-zinc-200">workers/gpu/colab_launcher.ipynb</code>.
          </p>
        </div>
      </div>

      <div className="rounded-xl border border-zinc-800 bg-[#111216] overflow-hidden">
        <div className="border-b border-zinc-800 px-5 py-3 text-xs font-semibold uppercase tracking-wider text-zinc-400">
          Registered Worker Fleet ({workers.length})
          {metrics && (
            <span className="ml-3 normal-case tracking-normal text-zinc-400">
              {metrics.workers.online} online · {metrics.workers.busy} busy · {metrics.workers.offline} offline · {metrics.workers.total_cu_month.toFixed(2)} CU this month
            </span>
          )}
        </div>
        <div className="divide-y divide-zinc-800/80">
          {workers.map((w) => (
            <div key={w.id} className="flex flex-wrap items-center justify-between gap-4 px-5 py-4 text-xs">
              <div className="flex items-center gap-3">
                <div className="rounded-lg bg-zinc-900 p-2 text-amber-400">
                  <Cpu className="h-4 w-4" />
                </div>
                <div>
                  <div className="flex items-center gap-2 font-semibold text-sm text-zinc-100">
                    {w.name}
                    <StatusBadge value={w.status} />
                  </div>
                  <div className="mt-0.5 text-zinc-400">
                    {w.provider} · {w.profile} · {w.gpu_model ?? "Awaiting register"} ({w.vram_gb ?? 0} GB)
                  </div>
                </div>
              </div>
              <div className="text-right">
                <div className="font-semibold text-amber-300">{w.cu_used_month.toFixed(2)} CU</div>
                <div className="text-[11px] text-zinc-500">{w.capabilities.join(", ")}</div>
                <button disabled={busy} aria-label={`Revoke ${w.name}`} onClick={() => revoke(w)}
                  className="mt-2 rounded border border-rose-500/30 px-2.5 py-1 text-xs text-rose-300">Revoke</button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

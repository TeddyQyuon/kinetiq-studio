"use client";

import React, { useEffect, useState } from "react";
import { Check, ClipboardCopy, Cpu, KeyRound, Terminal } from "lucide-react";
import type { WorkerRecord } from "@kinetiq/contracts";
import { api } from "@/lib/api-client";
import { StatusBadge } from "@/components/StatusBadge";

export default function WorkersPage() {
  const [workers, setWorkers] = useState<WorkerRecord[]>([]);
  const [isDemo, setIsDemo] = useState(false);
  const [name, setName] = useState("colab-l4-main");
  const [profile, setProfile] = useState("ltx25_pro");
  const [issuedToken, setIssuedToken] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  async function load() {
    const res = await api.listWorkers();
    setWorkers(res.data);
    setIsDemo(res.demo);
  }

  useEffect(() => {
    load();
  }, []);

  async function handleGenerateToken(e: React.FormEvent) {
    e.preventDefault();
    if (isDemo) {
      setIssuedToken("wrk_demo_start_fastapi_to_issue_real_tokens");
      return;
    }
    const res = await api.createWorkerToken(name, profile);
    setIssuedToken(res.worker_token);
    await load();
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6 md:p-8">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Google Colab GPU Workers &amp; Sync Hub</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Workers pull jobs over outbound HTTPS — no ngrok inbound ports, 15-min idle auto-shutdown protects your 200 CU/mo.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <form onSubmit={handleGenerateToken} className="rounded-xl border border-zinc-800 bg-[#111216] p-5 space-y-4">
          <div className="flex items-center gap-2 text-sm font-bold text-amber-300">
            <KeyRound className="h-4 w-4" />
            Connect Google Colab Runtime
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label className="text-xs text-zinc-400">Worker Name</label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-xs"
              />
            </div>
            <div>
              <label className="text-xs text-zinc-400">Model Profile</label>
              <select
                value={profile}
                onChange={(e) => setProfile(e.target.value)}
                className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-xs"
              >
                <option value="ltx25_pro">ltx25_pro (Qwen + LTX-2.5 1080p)</option>
                <option value="wan22_fast">wan22_fast (Qwen + Wan 2.2 4-step)</option>
                <option value="upscale_4k">upscale_4k (FlashVSR v1.1 4K)</option>
              </select>
            </div>
          </div>
          <button
            type="submit"
            className="rounded-lg bg-amber-500 px-4 py-2 text-xs font-semibold text-zinc-950 hover:bg-amber-400"
          >
            Generate Scoped Worker Token
          </button>

          {issuedToken && (
            <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-xs space-y-1.5">
              <div className="font-semibold text-emerald-300">Paste into Colab Secrets → WORKER_TOKEN:</div>
              <div className="flex items-center justify-between gap-2 font-mono text-[11px] bg-black/40 p-2 rounded">
                <span className="truncate">{issuedToken}</span>
                <button
                  type="button"
                  onClick={() => {
                    navigator.clipboard?.writeText(issuedToken);
                    setCopied(true);
                    setTimeout(() => setCopied(false), 1500);
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
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

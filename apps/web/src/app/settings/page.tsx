"use client";

import React, { useEffect, useState } from "react";
import { CheckCircle2, HardDrive, Key, Plug, Sparkles } from "lucide-react";
import { API_BASE, api, type IdentityStatus } from "@/lib/api-client";
import { StatusBadge } from "@/components/StatusBadge";

export default function SettingsPage() {
  const [identity, setIdentity] = useState<IdentityStatus | null>(null);
  const [isDemo, setIsDemo] = useState(false);

  useEffect(() => {
    api.getIdentity().then((res) => {
      setIdentity(res.data);
      setIsDemo(res.demo);
    });
  }, []);

  async function handleConnectDrive() {
    if (isDemo) return;
    const url = await api.startGoogleDriveAuth();
    window.location.href = url;
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6 md:p-8">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Studio Settings &amp; Integrations</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Connect Google Drive (`drive.file` least-privilege scope), Gemini AI Pro credits, and Antigravity MCP.
        </p>
      </div>

      <div className="rounded-xl border border-zinc-800 bg-[#111216] p-5 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <HardDrive className="h-5 w-5 text-emerald-400" />
            <div>
              <div className="text-sm font-bold">Google Drive Media Vault (2 TB)</div>
              <div className="text-xs text-zinc-400">
                Scope: <code>drive.file</code> — app accesses only its own <code>KinetiqStudio/</code> folder.
              </div>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <StatusBadge value={identity?.drive_connected ? "online" : "offline"} />
            <button
              onClick={handleConnectDrive}
              disabled={isDemo}
              className="rounded-lg bg-amber-500 px-3.5 py-2 text-xs font-semibold text-zinc-950 hover:bg-amber-400 disabled:opacity-50"
            >
              {identity?.drive_connected ? "Reconnect Drive" : "Connect Google Drive"}
            </button>
          </div>
        </div>
      </div>

      <div className="rounded-xl border border-zinc-800 bg-[#111216] p-5 space-y-3">
        <div className="flex items-center gap-2 text-sm font-bold text-amber-300">
          <Sparkles className="h-4 w-4" />
          AI Brain Configuration (.env)
        </div>
        <div className="grid gap-3 sm:grid-cols-2 text-xs">
          <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3 flex items-center justify-between">
            <span>Gemini API (Google AI Pro)</span>
            {identity?.gemini_configured ? (
              <span className="text-emerald-400 flex items-center gap-1">
                <CheckCircle2 className="h-3.5 w-3.5" /> Configured
              </span>
            ) : (
              <span className="text-zinc-400">Set GEMINI_API_KEY in .env</span>
            )}
          </div>
          <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3 flex items-center justify-between">
            <span>OpenAI API (Optional BYOK)</span>
            {identity?.openai_configured ? (
              <span className="text-emerald-400 flex items-center gap-1">
                <CheckCircle2 className="h-3.5 w-3.5" /> Configured
              </span>
            ) : (
              <span className="text-zinc-400">Optional</span>
            )}
          </div>
        </div>
      </div>

      <div className="rounded-xl border border-zinc-800 bg-[#111216] p-5 space-y-3 text-xs">
        <div className="flex items-center gap-2 text-sm font-bold text-cyan-300">
          <Plug className="h-4 w-4" />
          Antigravity Streamable HTTP MCP Endpoint
        </div>
        <p className="text-zinc-400">
          Connect Antigravity directly to your live declarative timeline editor using <code>MCP_ACCESS_TOKEN</code>:
        </p>
        <pre className="rounded-lg border border-zinc-800 bg-black/60 p-3 font-mono text-[11px] text-zinc-200 overflow-x-auto">
{`{
  "mcpServers": {
    "kinetiq-studio": {
      "url": "${API_BASE}/mcp",
      "headers": { "Authorization": "Bearer <YOUR_MCP_ACCESS_TOKEN>" }
    }
  }
}`}
        </pre>
        <div className="flex items-center gap-1.5 text-zinc-400">
          <Key className="h-3.5 w-3.5" />
          Tools exposed: <code>get_timeline_summary</code>, <code>get_scene</code>, <code>apply_editor_commands</code>
        </div>
      </div>
    </div>
  );
}

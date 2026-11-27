"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Cpu,
  Film,
  FolderOpen,
  HardDrive,
  PlusCircle,
  Settings,
  Sparkles,
} from "lucide-react";
import { api, AUTH_REQUIRED_EVENT, type IdentityStatus } from "@/lib/api-client";

const NAV_ITEMS = [
  { href: "/", label: "Library", icon: FolderOpen },
  { href: "/create", label: "Create Video", icon: PlusCircle },
  { href: "/workers", label: "GPU Workers", icon: Cpu },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function StudioShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [identity, setIdentity] = useState<IdentityStatus | null>(null);
  const [isDemo, setIsDemo] = useState(false);
  const [onlineWorkers, setOnlineWorkers] = useState(0);
  const [cuUsed, setCuUsed] = useState(0);

  const [authRequired, setAuthRequired] = useState(false);
  const [error, setError] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let active = true;
    const requireAuth = () => {
      setAuthRequired(true);
      setIsDemo(false);
      setError("");
      setCuUsed(0);
      setOnlineWorkers(0);
    };
    window.addEventListener(AUTH_REQUIRED_EVENT, requireAuth);
    (async () => {
      try {
        const session = await api.getIdentity();
        if (!active) return;
        setIdentity(session.data);
        setIsDemo(session.demo);
        setAuthRequired(Boolean(session.data.auth_required));
        setError("");
        if (session.data.auth_required) return;
        const workers = await api.listWorkers();
        if (!active) return;
        setOnlineWorkers(workers.data.filter((w) => w.status !== "offline").length);
        setCuUsed(Number(workers.data.reduce((acc, w) => acc + w.cu_used_month, 0).toFixed(1)));
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : "Could not load the workspace.");
      }
    })();
    return () => {
      active = false;
      window.removeEventListener(AUTH_REQUIRED_EVENT, requireAuth);
    };
  }, [pathname, retry]);

  async function signIn() {
    setConnecting(true);
    setError("");
    try {
      window.location.assign(await api.startGoogleDriveAuth());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start Google sign-in.");
      setConnecting(false);
    }
  }

  const isEditorRoute = pathname?.includes("/editor");

  return (
    <div className="flex min-h-screen bg-[#09090b] text-zinc-100">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-zinc-800/80 bg-[#0d0e12] md:flex">
        <div className="flex items-center gap-2.5 border-b border-zinc-800/80 px-5 py-4">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-amber-500/15 text-amber-400 border border-amber-500/30">
            <Film className="h-4 w-4" />
          </div>
          <div>
            <div className="text-sm font-bold tracking-tight">Kinetiq Studio</div>
            <div className="text-[11px] text-zinc-400">1080p / 4K AI Pipeline</div>
          </div>
        </div>

        <nav className="flex-1 space-y-1 px-3 py-4">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition ${
                  active
                    ? "bg-amber-500/15 text-amber-300 border border-amber-500/25"
                    : "text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-100"
                }`}
              >
                <Icon className="h-4 w-4" />
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="space-y-3 border-t border-zinc-800/80 p-4 text-xs">
          <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3">
            <div className="flex items-center justify-between text-zinc-300 font-medium">
              <span className="flex items-center gap-1.5">
                <Sparkles className="h-3.5 w-3.5 text-amber-400" />
                Colab Budget
              </span>
              <span className="text-amber-300">{cuUsed} / 200 CU</span>
            </div>
            <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-zinc-800">
              <div
                className="h-full rounded-full bg-amber-500"
                style={{ width: `${Math.min(100, (cuUsed / 200) * 100)}%` }}
              />
            </div>
            <div className="mt-2 flex items-center justify-between text-[11px] text-zinc-400">
              <span>Active GPUs: {onlineWorkers}</span>
              <span className="flex items-center gap-1">
                <HardDrive className="h-3 w-3" />
                {identity?.drive_connected ? "Drive Synced" : identity?.storage_provider ?? "local"}
              </span>
            </div>
          </div>

          <div className="flex items-center justify-between px-1 text-[11px] text-zinc-400">
            <span className="flex items-center gap-1.5">
              <span
                className={`h-2 w-2 rounded-full ${isDemo ? "bg-amber-400" : "bg-emerald-400"}`}
              />
              {authRequired ? "Owner sign-in required" : isDemo ? "Demo Workspace (API Offline)" : "Control Plane Online"}
            </span>
          </div>
        </div>
      </aside>

      <main className={`flex-1 ${isEditorRoute ? "overflow-hidden" : "overflow-y-auto"}`}>
        {authRequired ? (
          <section className="mx-auto mt-16 max-w-md rounded-xl border border-zinc-800 bg-zinc-900 p-6">
            <h1 className="text-xl font-semibold">Sign in to Kinetiq Studio</h1>
            <p className="mt-2 text-sm text-zinc-400">Use the Google account that owns this workspace.</p>
            <button disabled={connecting} onClick={signIn} className="mt-5 rounded-lg bg-amber-400 px-4 py-2 text-sm font-semibold text-zinc-950">
              {connecting ? "Opening Google…" : "Sign in with Google"}
            </button>
            {error && <p role="alert" className="mt-3 text-sm text-rose-300">{error}</p>}
          </section>
        ) : !identity ? (
          <div role="status" className="p-6 text-sm text-zinc-400">
            {error || "Opening workspace…"}
            {error && <button className="ml-3 underline" onClick={() => setRetry(value => value + 1)}>Retry</button>}
          </div>
        ) : children}
      </main>
    </div>
  );
}

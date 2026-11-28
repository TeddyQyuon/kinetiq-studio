import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET() {
  const base = (
    process.env.API_INTERNAL_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://localhost:8000"
  ).replace(/\/$/, "");

  try {
    const res = await fetch(`${base}/health`, {
      signal: AbortSignal.timeout(3000),
      cache: "no-store",
    });
    const health = res.ok ? await res.json() : null;
    return NextResponse.json({ online: res.ok || res.status === 401, env: health?.env });
  } catch {
    return NextResponse.json({ online: false });
  }
}

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
      signal: AbortSignal.timeout(500),
      cache: "no-store",
    });
    return NextResponse.json({ online: res.ok });
  } catch {
    return NextResponse.json({ online: false });
  }
}
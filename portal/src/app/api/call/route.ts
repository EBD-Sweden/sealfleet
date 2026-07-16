import { NextRequest, NextResponse } from "next/server";
import { requirePortalSession } from "@/lib/portal-auth";

const RUNTIME_URL = process.env.RUNTIME_URL || "http://localhost:8040";

export async function POST(req: NextRequest) {
  const { error, context } = await requirePortalSession();
  if (error) return error;

  try {
    const body = await req.json();
    const res = await fetch(`${RUNTIME_URL}/call`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...context?.backendHeaders },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "proxy error" },
      { status: 502 }
    );
  }
}

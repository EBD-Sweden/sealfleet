import { NextRequest, NextResponse } from "next/server";

const RUNTIME_URL = process.env.RUNTIME_URL || "http://localhost:8040";

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const limit = searchParams.get("limit") || "100";
    const server = searchParams.get("server") || "";
    const qs = new URLSearchParams({ limit, server });
    const res = await fetch(`${RUNTIME_URL}/audit/events?${qs}`, {
      cache: "no-store",
    });
    if (!res.ok) {
      return NextResponse.json(
        { error: "Runtime returned " + res.status },
        { status: res.status },
      );
    }
    const data = await res.json();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json(
      { error: "Failed to reach runtime" },
      { status: 502 },
    );
  }
}

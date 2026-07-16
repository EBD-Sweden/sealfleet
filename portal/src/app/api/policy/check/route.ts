import { NextRequest, NextResponse } from "next/server";
import { requirePortalSession } from "@/lib/portal-auth";

const RUNTIME_URL = process.env.RUNTIME_URL || "http://localhost:8040";

export async function POST(req: NextRequest) {
  const { error, context } = await requirePortalSession();
  if (error) return error;

  try {
    const body = await req.json();
    const res = await fetch(`${RUNTIME_URL}/policy/check`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...context?.backendHeaders },
      body: JSON.stringify(body),
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

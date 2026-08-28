import { NextResponse } from "next/server";

const AGENT_URL = process.env.AGENT_URL || "http://localhost:8050";

/** Which brains the chat can plan with (local on-box model, cloud provider, …). */
export async function GET() {
  try {
    const upstream = await fetch(`${AGENT_URL}/models`, {
      // Deployment config, not per-user data — but it can change when an
      // operator starts/stops a local model, so don't cache it.
      cache: "no-store",
    });

    if (!upstream.ok) {
      return NextResponse.json(
        { default: null, models: [], error: `Agent returned ${upstream.status}` },
        { status: upstream.status },
      );
    }

    return NextResponse.json(await upstream.json());
  } catch {
    return NextResponse.json(
      { default: null, models: [], error: "Failed to reach core agent" },
      { status: 502 },
    );
  }
}

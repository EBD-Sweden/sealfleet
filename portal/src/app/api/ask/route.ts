import { NextRequest } from "next/server";

const AGENT_URL = process.env.AGENT_URL || "http://localhost:8050";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const upstream = await fetch(`${AGENT_URL}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!upstream.ok) {
      const text = await upstream.text();
      return new Response(
        JSON.stringify({ error: text || `Agent returned ${upstream.status}` }),
        { status: upstream.status, headers: { "Content-Type": "application/json" } },
      );
    }

    const data = await upstream.json();
    return new Response(JSON.stringify(data), {
      headers: { "Content-Type": "application/json" },
    });
  } catch {
    return new Response(
      JSON.stringify({ error: "Failed to reach core agent" }),
      { status: 502, headers: { "Content-Type": "application/json" } },
    );
  }
}

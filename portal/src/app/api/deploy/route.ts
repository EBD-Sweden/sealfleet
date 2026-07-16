import { NextRequest } from "next/server";
import { requirePortalSession } from "@/lib/portal-auth";

const DEPLOY_URL = process.env.DEPLOY_URL || "http://localhost:8030";

export async function POST(req: NextRequest) {
  const { error, context } = await requirePortalSession();
  if (error) return error;

  try {
    const body = await req.json();
    const upstream = await fetch(`${DEPLOY_URL}/deploy`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...context?.backendHeaders },
      body: JSON.stringify(body),
    });

    if (!upstream.ok || !upstream.body) {
      const text = await upstream.text();
      return new Response(JSON.stringify({ error: text || `Deploy service returned ${upstream.status}` }), {
        status: upstream.status,
        headers: { "Content-Type": "application/json" },
      });
    }

    // Pass through SSE stream
    return new Response(upstream.body, {
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      },
    });
  } catch {
    return new Response(JSON.stringify({ error: "Failed to reach deploy service" }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
}

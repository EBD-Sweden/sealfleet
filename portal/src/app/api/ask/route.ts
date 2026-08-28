import { NextRequest, NextResponse } from "next/server";
import { requirePortalSession } from "@/lib/portal-auth";

const AGENT_URL = process.env.AGENT_URL || "http://localhost:8050";

/** Identity headers only — the agent presents its own backend key downstream. */
function delegationHeaders(backendHeaders: Record<string, string>) {
  const out: Record<string, string> = { "Content-Type": "application/json" };
  for (const h of ["X-McpFinder-User-Id", "X-McpFinder-Tenant-Id", "X-McpFinder-Groups"]) {
    if (backendHeaders[h]) out[h] = backendHeaders[h];
  }
  return out;
}

export async function POST(req: NextRequest) {
  // The chat may resolve the tenant's own LLM credential, so it must run as the
  // logged-in user rather than as an anonymous service call.
  const { error, context } = await requirePortalSession();
  if (error || !context) return error ?? NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  try {
    const body = await req.json();
    const upstream = await fetch(`${AGENT_URL}/ask`, {
      method: "POST",
      headers: delegationHeaders(context.backendHeaders),
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

import { NextResponse } from "next/server";
import { requirePortalSession } from "@/lib/portal-auth";

const AGENT_URL = process.env.AGENT_URL || "http://localhost:8050";

/** Which brains this user can plan with (local, cloud, or their tenant's own provider). */
export async function GET() {
  // Tenant-scoped: the list includes the tenant's own LLM credentials.
  const { error, context } = await requirePortalSession();
  if (error || !context) return error ?? NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const headers: Record<string, string> = {};
  for (const h of ["X-McpFinder-User-Id", "X-McpFinder-Tenant-Id", "X-McpFinder-Groups"]) {
    if (context.backendHeaders[h]) headers[h] = context.backendHeaders[h];
  }

  try {
    const upstream = await fetch(`${AGENT_URL}/models`, { headers, cache: "no-store" });

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

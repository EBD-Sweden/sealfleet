import { NextRequest, NextResponse } from "next/server";
import { requirePortalSession } from "@/lib/portal-auth";

const ROUTER = process.env.ROUTER_URL || "http://mcp-router:8040";

export const dynamic = "force-dynamic";

export async function GET() {
  const { error, context } = await requirePortalSession();
  if (error) return error;

  try {
    const r = await fetch(`${ROUTER}/credentials`, {
      cache: "no-store",
      headers: context?.backendHeaders,
    });
    if (!r.ok) {
      return NextResponse.json(
        { error: "Router returned " + r.status },
        { status: r.status },
      );
    }
    return NextResponse.json(await r.json(), { status: r.status });
  } catch {
    return NextResponse.json(
      { error: "Failed to reach router" },
      { status: 502 },
    );
  }
}

export async function POST(req: NextRequest) {
  const { error, context } = await requirePortalSession();
  if (error) return error;

  try {
    const body = await req.json();
    const r = await fetch(`${ROUTER}/credentials`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...context?.backendHeaders },
      body: JSON.stringify(body),
    });
    return NextResponse.json(await r.json(), { status: r.status });
  } catch {
    return NextResponse.json(
      { error: "Failed to reach router" },
      { status: 502 },
    );
  }
}

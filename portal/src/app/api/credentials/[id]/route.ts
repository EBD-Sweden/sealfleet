import { NextRequest, NextResponse } from "next/server";

const ROUTER = process.env.ROUTER_URL || "http://mcp-router:8040";

export const dynamic = "force-dynamic";

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await params;
    const body = await req.json();
    const r = await fetch(`${ROUTER}/credentials/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
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

export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await params;
    const r = await fetch(`${ROUTER}/credentials/${id}`, {
      method: "DELETE",
    });
    if (r.status === 204) {
      return new NextResponse(null, { status: 204 });
    }
    return NextResponse.json(await r.json(), { status: r.status });
  } catch {
    return NextResponse.json(
      { error: "Failed to reach router" },
      { status: 502 },
    );
  }
}

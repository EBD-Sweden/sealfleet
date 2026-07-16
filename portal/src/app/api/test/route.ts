import { NextRequest, NextResponse } from "next/server";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { serverName, tool, inputs } = body as {
      serverName: string;
      tool: string;
      inputs: Record<string, unknown>;
    };


    return NextResponse.json(
      { error: `Unknown server: ${serverName}` },
      { status: 400 }
    );
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

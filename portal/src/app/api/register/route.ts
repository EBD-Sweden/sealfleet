import { NextRequest, NextResponse } from "next/server";

const REGISTRY_URL = process.env.REGISTRY_URL || "http://localhost:8010";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const res = await fetch(`${REGISTRY_URL}/servers`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text();
      return NextResponse.json(
        { error: text || "Registry returned " + res.status },
        { status: res.status },
      );
    }
    const data = await res.json();
    return NextResponse.json(data);
  } catch (e) {
    return NextResponse.json(
      { error: "Failed to reach registry" },
      { status: 502 },
    );
  }
}

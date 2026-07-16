import { NextResponse } from "next/server";

const RUNTIME_URL = process.env.RUNTIME_URL || "http://localhost:8040";

export async function GET() {
  try {
    const res = await fetch(`${RUNTIME_URL}/pipelines`, {
      headers: { "Content-Type": "application/json" },
      // No caching — pipeline list can change
      cache: "no-store",
    });

    if (!res.ok) {
      const text = await res.text();
      return NextResponse.json(
        { error: `Runtime error: ${res.status}`, detail: text },
        { status: res.status },
      );
    }

    const data = await res.json();
    return NextResponse.json(data);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Connection failed";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}

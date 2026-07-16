import { NextResponse } from "next/server";

const RUNTIME_URL = process.env.RUNTIME_URL || "http://localhost:8040";

export async function POST() {
  try {
    const res = await fetch(`${RUNTIME_URL}/policy/reload`, {
      method: "POST",
    });
    if (!res.ok) {
      return NextResponse.json(
        { error: "Runtime returned " + res.status },
        { status: res.status },
      );
    }
    const data = await res.json();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json(
      { error: "Failed to reach runtime" },
      { status: 502 },
    );
  }
}

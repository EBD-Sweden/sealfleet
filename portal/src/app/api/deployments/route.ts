import { NextResponse } from "next/server";

const DEPLOY_URL = process.env.DEPLOY_URL || "http://localhost:8030";

export async function GET() {
  try {
    const res = await fetch(`${DEPLOY_URL}/deployments`, { cache: "no-store" });
    if (!res.ok) {
      return NextResponse.json({ error: "Deploy service returned " + res.status }, { status: res.status });
    }
    const data = await res.json();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({ error: "Failed to reach deploy service" }, { status: 502 });
  }
}

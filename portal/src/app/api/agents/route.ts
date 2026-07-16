import { NextResponse } from "next/server";

const AGENTS_API = process.env.AGENTS_API_URL || "http://host.k3d.internal:3099";

export async function GET() {
  try {
    const r = await fetch(`${AGENTS_API}/agents`, { next: { revalidate: 0 } });
    const d = await r.json();
    return NextResponse.json(d);
  } catch (e) {
    return NextResponse.json({ error: String(e), agents: [] }, { status: 500 });
  }
}

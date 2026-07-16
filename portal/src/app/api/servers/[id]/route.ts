import { NextRequest, NextResponse } from "next/server";

const REGISTRY_URL = process.env.REGISTRY_URL || "http://localhost:8010";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  try {
    const res = await fetch(`${REGISTRY_URL}/servers/${id}`, {
      cache: "no-store",
    });
    if (!res.ok) {
      return NextResponse.json(
        { error: "Registry returned " + res.status },
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

import { NextRequest, NextResponse } from "next/server";
import { requirePortalSession } from "@/lib/portal-auth";

const RUNTIME_URL = process.env.RUNTIME_URL || "http://localhost:8040";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { error } = await requirePortalSession();
  if (error) return error;

  const { id } = await params;
  return NextResponse.json(
    {
      error: "Forbidden",
      detail: `Sealed handle ${id} can only be resolved by authorized execution paths`,
    },
    { status: 403 },
  );
}

export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { error, context } = await requirePortalSession();
  if (error) return error;

  const { id } = await params;
  try {
    const res = await fetch(`${RUNTIME_URL}/sealed/${id}`, {
      method: "DELETE",
      headers: context?.backendHeaders,
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
  } catch {
    return NextResponse.json(
      { error: "Failed to reach runtime" },
      { status: 502 },
    );
  }
}

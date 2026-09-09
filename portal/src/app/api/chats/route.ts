import { NextRequest, NextResponse } from "next/server";
import { requirePortalSession } from "@/lib/portal-auth";
import { createChat, listChats } from "@/lib/chats";

export const dynamic = "force-dynamic";

export async function GET() {
  const { error, context } = await requirePortalSession();
  if (error || !context) return error ?? NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const { user } = context;
  try {
    return NextResponse.json({ chats: await listChats(user.tenant_id ?? "default", user.id) });
  } catch {
    return NextResponse.json({ error: "Failed to list chats" }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  const { error, context } = await requirePortalSession();
  if (error || !context) return error ?? NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const { user } = context;
  try {
    const body = await req.json().catch(() => ({}));
    const chat = await createChat(user.tenant_id ?? "default", user.id, String(body.title ?? "New chat"));
    return NextResponse.json({ chat }, { status: 201 });
  } catch {
    return NextResponse.json({ error: "Failed to create chat" }, { status: 500 });
  }
}

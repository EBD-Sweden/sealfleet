import { NextRequest, NextResponse } from "next/server";
import { requirePortalSession } from "@/lib/portal-auth";
import { deleteChat, getChat, listMessages } from "@/lib/chats";

export const dynamic = "force-dynamic";

type Params = { params: Promise<{ id: string }> };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function GET(_req: NextRequest, { params }: Params) {
  const { error, context } = await requirePortalSession();
  if (error || !context) return error ?? NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const { user } = context;
  const { id } = await params;
  if (!UUID.test(id)) return NextResponse.json({ error: "Not found" }, { status: 404 });
  try {
    const chat = await getChat(user.tenant_id ?? "default", user.id, id);
    if (!chat) return NextResponse.json({ error: "Not found" }, { status: 404 });
    const messages = await listMessages(user.tenant_id ?? "default", user.id, id);
    return NextResponse.json({ chat, messages: messages ?? [] });
  } catch {
    return NextResponse.json({ error: "Failed to load chat" }, { status: 500 });
  }
}

export async function DELETE(_req: NextRequest, { params }: Params) {
  const { error, context } = await requirePortalSession();
  if (error || !context) return error ?? NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const { user } = context;
  const { id } = await params;
  if (!UUID.test(id)) return NextResponse.json({ error: "Not found" }, { status: 404 });
  try {
    const gone = await deleteChat(user.tenant_id ?? "default", user.id, id);
    if (!gone) return NextResponse.json({ error: "Not found" }, { status: 404 });
    return new NextResponse(null, { status: 204 });
  } catch {
    return NextResponse.json({ error: "Failed to delete chat" }, { status: 500 });
  }
}

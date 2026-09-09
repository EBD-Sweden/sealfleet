import { NextRequest, NextResponse } from "next/server";
import { requirePortalSession } from "@/lib/portal-auth";
import { appendMessage, createChat, getChat, recentTurns } from "@/lib/chats";

const AGENT_URL = process.env.AGENT_URL || "http://localhost:8050";

/** Identity headers only — the agent presents its own backend key downstream. */
function delegationHeaders(backendHeaders: Record<string, string>) {
  const out: Record<string, string> = { "Content-Type": "application/json" };
  for (const h of ["X-API-Key", "X-Sealfleet-User-Id", "X-Sealfleet-Tenant-Id", "X-Sealfleet-Groups"]) {
    if (backendHeaders[h]) out[h] = backendHeaders[h];
  }
  return out;
}

export async function POST(req: NextRequest) {
  // The chat may resolve the tenant's own LLM credential, so it must run as the
  // logged-in user rather than as an anonymous service call.
  const { error, context } = await requirePortalSession();
  if (error || !context) return error ?? NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  try {
    const body = await req.json();
    const { user } = context;
    const tenantId = user.tenant_id ?? "default";
    const question = String(body.question ?? "").trim();

    // Resolve the conversation: verify a supplied chat_id belongs to this
    // user, or start a new chat titled after the first question. History is
    // read BEFORE this question is stored, so it holds only prior turns.
    let chatId: string | null = typeof body.chat_id === "string" ? body.chat_id : null;
    let history: { role: string; content: string }[] = [];
    try {
      if (chatId) {
        if (!(await getChat(tenantId, user.id, chatId))) {
          return NextResponse.json({ error: "Chat not found" }, { status: 404 });
        }
        history = await recentTurns(tenantId, user.id, chatId);
      } else if (question) {
        chatId = (await createChat(tenantId, user.id, question)).id;
      }
      if (chatId && question) await appendMessage(chatId, "user", question);
    } catch (e) {
      // History is an enhancement; the question must still be answered.
      console.error("[ask] chat persistence unavailable:", e);
      chatId = null;
      history = [];
    }

    const { chat_id: _drop, ...forward } = body;
    const upstream = await fetch(`${AGENT_URL}/ask`, {
      method: "POST",
      headers: delegationHeaders(context.backendHeaders),
      body: JSON.stringify({ ...forward, history }),
    });

    if (!upstream.ok) {
      const text = await upstream.text();
      return new Response(
        JSON.stringify({ error: text || `Agent returned ${upstream.status}`, chat_id: chatId }),
        { status: upstream.status, headers: { "Content-Type": "application/json" } },
      );
    }

    const data = await upstream.json();
    if (chatId) {
      try {
        await appendMessage(chatId, "agent", String(data.answer ?? ""), data);
      } catch (e) {
        console.error("[ask] failed to store agent turn:", e);
      }
    }
    return new Response(JSON.stringify({ ...data, chat_id: chatId }), {
      headers: { "Content-Type": "application/json" },
    });
  } catch {
    return new Response(
      JSON.stringify({ error: "Failed to reach core agent" }),
      { status: 502, headers: { "Content-Type": "application/json" } },
    );
  }
}

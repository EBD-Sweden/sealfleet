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

  let chatId: string | null = null;
  try {
    const raw = await req.json().catch(() => null);
    // A null/non-object body must not TypeError into the generic 502 path.
    const body: Record<string, unknown> =
      raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
    const { user } = context;
    const tenantId = user.tenant_id ?? "default";
    const question = String(body.question ?? "").trim();
    if (!question) {
      // Reject before anything is stored, so no agent turn can exist without
      // its user turn and no chat is created for an empty question.
      return NextResponse.json({ error: "question is required" }, { status: 400 });
    }

    // Resolve the conversation: verify a supplied chat_id belongs to this
    // user, or start a new chat titled after the first question. History is
    // read BEFORE this question is stored, so it holds only prior turns.
    const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    const requested = typeof body.chat_id === "string" ? body.chat_id : null;
    // A malformed id is a client error, not "persistence unavailable" — and it
    // must not reach Postgres, whose uuid cast error would be misread below.
    if (requested && !UUID.test(requested)) {
      return NextResponse.json({ error: "Chat not found" }, { status: 404 });
    }

    let history: { role: string; content: string }[] = [];
    try {
      if (requested) {
        if (!(await getChat(tenantId, user.id, requested))) {
          return NextResponse.json({ error: "Chat not found" }, { status: 404 });
        }
        chatId = requested;
        history = await recentTurns(tenantId, user.id, chatId);
      } else {
        chatId = (await createChat(tenantId, user.id, question)).id;
      }
    } catch (e) {
      // History/persistence is an enhancement; the question must still be answered.
      console.error("[ask] chat persistence unavailable:", e);
      chatId = null;
      history = [];
    }
    if (chatId) {
      // Separate try: if the chat row exists but this write fails transiently,
      // keep chatId so the client adopts the chat instead of orphaning it.
      try {
        await appendMessage(chatId, "user", question);
      } catch (e) {
        console.error("[ask] failed to store user turn:", e);
      }
    }

    const { chat_id: _drop, ...forward } = body;
    const upstream = await fetch(`${AGENT_URL}/ask`, {
      method: "POST",
      headers: delegationHeaders(context.backendHeaders),
      body: JSON.stringify({ ...forward, question, history }),
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
      JSON.stringify({ error: "Failed to reach core agent", chat_id: chatId }),
      { status: 502, headers: { "Content-Type": "application/json" } },
    );
  }
}

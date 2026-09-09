import type { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { sessionMock, chatsMock, fetchMock } = vi.hoisted(() => ({
  sessionMock: vi.fn(),
  chatsMock: {
    createChat: vi.fn(),
    getChat: vi.fn(),
    recentTurns: vi.fn(),
    appendMessage: vi.fn(),
  },
  fetchMock: vi.fn(),
}));

vi.mock("@/lib/portal-auth", () => ({ requirePortalSession: sessionMock }));
vi.mock("@/lib/chats", () => chatsMock);
vi.stubGlobal("fetch", fetchMock);

import { POST } from "@/app/api/ask/route";

const ctx = { user: { id: "u1", tenant_id: "t1" }, backendHeaders: { "X-API-Key": "k" } };
const req = (body: unknown) => ({ json: async () => body }) as unknown as NextRequest;

beforeEach(() => {
  vi.clearAllMocks();
  sessionMock.mockResolvedValue({ error: null, context: ctx });
  chatsMock.createChat.mockResolvedValue({ id: "11111111-2222-3333-4444-555555555555" });
  chatsMock.getChat.mockResolvedValue({ id: "c" });
  chatsMock.recentTurns.mockResolvedValue([]);
  chatsMock.appendMessage.mockResolvedValue(undefined);
  fetchMock.mockResolvedValue({ ok: true, json: async () => ({ answer: "hi" }) });
});

describe("ask route hardening", () => {
  it("a JSON null body is a 400, not a TypeError disguised as 502", async () => {
    const res = (await POST(req(null))) as Response;
    expect(res.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("a whitespace-only question is rejected before anything is stored", async () => {
    const res = (await POST(req({ question: "   " }))) as Response;
    expect(res.status).toBe(400);
    expect(chatsMock.createChat).not.toHaveBeenCalled();
    expect(chatsMock.appendMessage).not.toHaveBeenCalled();
  });

  it("a non-UUID chat_id is a 404 and never reaches Postgres", async () => {
    const res = (await POST(req({ question: "hi", chat_id: "abc" }))) as Response;
    expect(res.status).toBe(404);
    expect(chatsMock.getChat).not.toHaveBeenCalled();
  });

  it("an agent failure still returns the chat_id that was already created", async () => {
    fetchMock.mockRejectedValue(new Error("boom"));
    const res = (await POST(req({ question: "hi" }))) as Response;
    expect(res.status).toBe(502);
    const body = JSON.parse(await res.text());
    expect(body.chat_id).toBe("11111111-2222-3333-4444-555555555555");
  });

  it("a failing user-turn write keeps the chat instead of orphaning it", async () => {
    chatsMock.appendMessage.mockRejectedValueOnce(new Error("db blip"));
    const res = (await POST(req({ question: "hi" }))) as Response;
    const body = JSON.parse(await res.text());
    expect(body.chat_id).toBe("11111111-2222-3333-4444-555555555555");
  });
});

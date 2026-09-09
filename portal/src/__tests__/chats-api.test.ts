import type { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { sessionMock, queryMock } = vi.hoisted(() => ({
  sessionMock: vi.fn(),
  queryMock: vi.fn(),
}));

vi.mock("@/lib/portal-auth", () => ({ requirePortalSession: sessionMock }));
vi.mock("@/lib/db", () => ({ pool: { query: queryMock } }));

import { GET as listChats, POST as createChat } from "@/app/api/chats/route";
import { DELETE as deleteChat, GET as getChat } from "@/app/api/chats/[id]/route";

const USER = { id: "u1", tenant_id: "t1" };
const ctx = { user: USER, backendHeaders: {} };
const authed = () => sessionMock.mockResolvedValue({ error: null, context: ctx });
const anon = () =>
  sessionMock.mockResolvedValue({ error: { status: 401 } as unknown as Response, context: null });

function req(body?: unknown): NextRequest {
  return { json: async () => body ?? {} } as unknown as NextRequest;
}
const params = (id: string) => ({ params: Promise.resolve({ id }) });

beforeEach(() => {
  vi.clearAllMocks();
});

describe("chats API", () => {
  it("every route refuses anonymous callers", async () => {
    anon();
    expect(((await listChats()) as { status?: number }).status).toBe(401);
    expect(((await createChat(req())) as { status?: number }).status).toBe(401);
    expect(((await getChat(req(), params("c1"))) as { status?: number }).status).toBe(401);
    expect(((await deleteChat(req(), params("c1"))) as { status?: number }).status).toBe(401);
    expect(queryMock).not.toHaveBeenCalled();
  });

  it("list is scoped to the session user, newest first", async () => {
    authed();
    queryMock.mockResolvedValue({ rows: [{ id: "c1", title: "Weather" }] });
    const res = await listChats();
    expect(await (res as Response).json()).toEqual({ chats: [{ id: "c1", title: "Weather" }] });
    const [sql, args] = queryMock.mock.calls[0];
    expect(sql).toMatch(/tenant_id = \$1 AND user_id = \$2/);
    expect(sql).toMatch(/ORDER BY updated_at DESC/);
    expect(args.slice(0, 2)).toEqual(["t1", "u1"]);
  });

  it("create titles the chat and returns 201", async () => {
    authed();
    queryMock.mockResolvedValue({ rows: [{ id: "c2", title: "Hello" }] });
    const res = await createChat(req({ title: "Hello" }));
    expect((res as Response).status).toBe(201);
    expect(queryMock.mock.calls[0][1]).toEqual(["t1", "u1", "Hello"]);
  });

  it("a chat owned by someone else is a 404, and its messages are never read", async () => {
    authed();
    queryMock.mockResolvedValue({ rows: [] }); // ownership lookup misses
    const res = await getChat(req(), params("theirs"));
    expect((res as Response).status).toBe(404);
    expect(queryMock).toHaveBeenCalledTimes(1);
  });

  it("get returns the chat with its messages", async () => {
    authed();
    queryMock
      .mockResolvedValueOnce({ rows: [{ id: "c1", title: "Weather" }] }) // getChat (route)
      .mockResolvedValueOnce({ rows: [{ id: "c1", title: "Weather" }] }) // getChat (listMessages guard)
      .mockResolvedValueOnce({ rows: [{ role: "user", content: "hi", payload: null }] });
    const body = await ((await getChat(req(), params("c1"))) as Response).json();
    expect(body.messages).toHaveLength(1);
  });

  it("delete answers 204 when a row went away and 404 otherwise", async () => {
    authed();
    queryMock.mockResolvedValueOnce({ rowCount: 1 });
    expect(((await deleteChat(req(), params("c1"))) as Response).status).toBe(204);
    queryMock.mockResolvedValueOnce({ rowCount: 0 });
    expect(((await deleteChat(req(), params("c1"))) as Response).status).toBe(404);
    for (const [, args] of queryMock.mock.calls) expect(args.slice(0, 2)).toEqual(["t1", "u1"]);
  });
});

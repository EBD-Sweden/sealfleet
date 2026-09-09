import { pool } from "@/lib/db";

/** Chat history for the Ask page. Every query is scoped to (tenant, user):
 *  a chat id from another user simply does not exist for this one. */

export interface ChatSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface StoredMessage {
  id: string;
  role: "user" | "agent";
  content: string;
  payload: Record<string, unknown> | null;
  created_at: string;
}

const SCOPE = "tenant_id = $1 AND user_id = $2";

export async function listChats(tenantId: string, userId: string, limit = 50): Promise<ChatSummary[]> {
  const r = await pool.query(
    `SELECT id, title, created_at, updated_at FROM chats
     WHERE ${SCOPE} ORDER BY updated_at DESC LIMIT $3`,
    [tenantId, userId, limit],
  );
  return r.rows;
}

export async function createChat(tenantId: string, userId: string, title: string): Promise<ChatSummary> {
  const r = await pool.query(
    `INSERT INTO chats (tenant_id, user_id, title) VALUES ($1, $2, $3)
     RETURNING id, title, created_at, updated_at`,
    [tenantId, userId, title.slice(0, 120) || "New chat"],
  );
  return r.rows[0];
}

export async function getChat(tenantId: string, userId: string, chatId: string): Promise<ChatSummary | null> {
  const r = await pool.query(
    `SELECT id, title, created_at, updated_at FROM chats WHERE ${SCOPE} AND id = $3`,
    [tenantId, userId, chatId],
  );
  return r.rows[0] ?? null;
}

export async function listMessages(tenantId: string, userId: string, chatId: string): Promise<StoredMessage[] | null> {
  if (!(await getChat(tenantId, userId, chatId))) return null;
  const r = await pool.query(
    `SELECT id, role, content, payload, created_at FROM chat_messages
     WHERE chat_id = $1 ORDER BY created_at, id`,
    [chatId],
  );
  return r.rows;
}

export async function appendMessage(
  chatId: string, role: "user" | "agent", content: string, payload?: unknown,
): Promise<void> {
  await pool.query(
    `INSERT INTO chat_messages (chat_id, role, content, payload) VALUES ($1, $2, $3, $4)`,
    [chatId, role, content, payload == null ? null : JSON.stringify(payload)],
  );
  await pool.query(`UPDATE chats SET updated_at = NOW() WHERE id = $1`, [chatId]);
}

/** Last `limit` turns, oldest first — the context replayed to the agent. */
export async function recentTurns(
  tenantId: string, userId: string, chatId: string, limit = 8,
): Promise<{ role: string; content: string }[]> {
  if (!(await getChat(tenantId, userId, chatId))) return [];
  const r = await pool.query(
    `SELECT role, content FROM (
       SELECT role, content, created_at, id FROM chat_messages
       WHERE chat_id = $1 ORDER BY created_at DESC, id DESC LIMIT $2
     ) t ORDER BY created_at, id`,
    [chatId, limit],
  );
  return r.rows;
}

export async function deleteChat(tenantId: string, userId: string, chatId: string): Promise<boolean> {
  const r = await pool.query(`DELETE FROM chats WHERE ${SCOPE} AND id = $3`, [tenantId, userId, chatId]);
  return (r.rowCount ?? 0) > 0;
}

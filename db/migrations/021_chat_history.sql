-- Chat history for the portal's Ask page: conversations and their messages,
-- scoped to the logged-in user (and tenant). The agent stays stateless; the
-- portal persists both sides of each exchange and replays recent turns as
-- context so follow-up questions work.

CREATE TABLE IF NOT EXISTS chats (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    -- First question, trimmed; editable later if we add renaming.
    title       TEXT NOT NULL DEFAULT 'New chat',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chats_user
    ON chats(tenant_id, user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id     UUID NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('user', 'agent')),
    content     TEXT NOT NULL,
    -- Full AskResponse for agent turns (resolved_chain, raw_result, ...),
    -- so reopening a chat restores the chain badges and raw results.
    payload     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_chat
    ON chat_messages(chat_id, created_at);

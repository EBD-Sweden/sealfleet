-- Credential store for MCP secrets and tenant-owned LLM provider keys.
--
-- The /credentials endpoints, runtime/credentials.py token resolution
-- ({{credential:name}}), and the portal's Credentials page all read this
-- table, but no migration created it — every deployment built from these
-- migrations answered those routes with UndefinedTable. This adds it.
--
-- Values are never stored in plaintext:
--   platform — AES-256-GCM (Fernet) with the deployment's ENCRYPTION_KEY
--   byok     — encrypted with a key the tenant holds; the platform cannot read it
--   k8s      — not stored here at all; only a pointer to a Kubernetes Secret
-- The LLM never sees a plaintext value: it is resolved at MCP call time.

CREATE TABLE IF NOT EXISTS credentials (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    secret_type     TEXT NOT NULL DEFAULT 'api_key',

    -- NULL for storage_mode='k8s', where the value lives in a k8s Secret.
    encrypted_value TEXT,
    storage_mode    TEXT NOT NULL DEFAULT 'platform',
    k8s_secret_name TEXT,
    k8s_secret_key  TEXT,

    -- What may use this secret. Reserved prefix "llm:<provider>[:<model>]"
    -- marks a credential the chat may use as a bring-your-own LLM provider
    -- ("llm:custom:<model>@<base_url>" for a private endpoint).
    assigned_mcp    TEXT,

    tenant_id       TEXT NOT NULL,
    created_by      TEXT NOT NULL DEFAULT 'admin',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    last_used_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Every read is tenant-scoped; resolution is by (name, tenant_id).
CREATE INDEX IF NOT EXISTS idx_credentials_tenant ON credentials(tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_credentials_tenant_name
    ON credentials(tenant_id, name);
CREATE INDEX IF NOT EXISTS idx_credentials_active
    ON credentials(tenant_id) WHERE is_active = TRUE;

-- A k8s-backed credential carries a pointer instead of a value; every other
-- mode must actually hold one.
ALTER TABLE credentials DROP CONSTRAINT IF EXISTS credentials_value_present;
ALTER TABLE credentials ADD CONSTRAINT credentials_value_present CHECK (
    (storage_mode = 'k8s'  AND k8s_secret_name IS NOT NULL AND k8s_secret_key IS NOT NULL)
    OR (storage_mode <> 'k8s' AND encrypted_value IS NOT NULL)
);

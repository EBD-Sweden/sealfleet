-- 008_api_key_identity_delegation.sql — explicit portal delegated identity privilege
-- Run: PGPASSWORD=admin psql -h localhost -p 54323 -U admin -d mcpfinder -f db/migrations/008_api_key_identity_delegation.sql

BEGIN;

-- Ensure the DB-managed API key table exists for fresh production installs.
-- Existing deployments keep their current rows; the ALTER statements below are
-- idempotent.
CREATE TABLE IF NOT EXISTS api_keys (
    api_key TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    is_active BOOLEAN DEFAULT true,
    action_permissions TEXT[]
);

-- Runtime API keys are tenant-scoped service identities by default. Portal
-- backend keys that need to bind sealed handles to the end user must be opted in
-- explicitly; the router must never infer this privilege from a human-readable
-- key name.
ALTER TABLE api_keys
  ADD COLUMN IF NOT EXISTS allow_identity_delegation BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE api_keys
  ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_api_keys_identity_delegation
  ON api_keys(allow_identity_delegation)
  WHERE allow_identity_delegation = true;

COMMIT;

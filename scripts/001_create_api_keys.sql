-- Migration: Create api_keys table for router authentication
-- Run: psql postgresql://admin:admin@localhost:54323/mcpfinder < scripts/001_create_api_keys.sql

CREATE TABLE IF NOT EXISTS api_keys (
    api_key TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    is_active BOOLEAN DEFAULT true
);

CREATE INDEX IF NOT EXISTS idx_api_keys_tenant ON api_keys(tenant_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_active ON api_keys(is_active) WHERE is_active = true;

-- The router enforces per-action permissions from api_keys.action_permissions
-- (see runtime/router.py:_authorize_action). The column is added by later
-- migrations; ensure it exists so this bootstrap is self-contained.
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS action_permissions TEXT[];
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS allow_identity_delegation BOOLEAN NOT NULL DEFAULT false;

-- Insert default development key (for local dev).
--
-- Grants a least-privilege "agent operator" action set so local LLM agents and
-- the mcpfinder CLI can run the documented lifecycle out-of-box:
--   pipeline.invoke    -> invoke tools, run pipelines, submit jobs (workflows)
--   agent.invoke       -> dispatch to external/core agents and agent-backed jobs
--   agent.register     -> register external agents
--   mcp.server.register-> register MCP manifests
--   registry.export    -> export the registry control-plane snapshot
--   registry.import    -> import a registry snapshot
-- Deliberately EXCLUDES privileged actions (policy.admin, credential.*,
-- sealed_handle.*, audit.read) — local preview should not ship an admin key.
--
-- allow_identity_delegation=true lets the portal (which presents this key when
-- proxying user actions) pass the logged-in user/tenant via X-McpFinder-* headers
-- so router audit rows and tenant scoping reflect the real user, not the key
-- (see runtime/router.py:_api_key_allows_identity_delegation).
INSERT INTO api_keys (api_key, tenant_id, name, is_active, action_permissions, allow_identity_delegation)
VALUES (
    'loHPxndS1CBmn-dLHBZwvliXV7ixPyWU_5hlZo-VwpA',
    'local-dev',
    'Local Development Key',
    true,
    ARRAY['pipeline.invoke','agent.invoke','agent.register','mcp.server.register','registry.export','registry.import'],
    true
)
ON CONFLICT (api_key) DO UPDATE
    SET action_permissions = EXCLUDED.action_permissions,
        allow_identity_delegation = EXCLUDED.allow_identity_delegation,
        is_active = true;

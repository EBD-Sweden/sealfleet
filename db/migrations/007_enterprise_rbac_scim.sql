-- 007_enterprise_rbac_scim.sql — runtime action RBAC + SCIM lifecycle storage
-- Run: PGPASSWORD=admin psql -h localhost -p 54323 -U admin -d mcpfinder -f db/migrations/007_enterprise_rbac_scim.sql

BEGIN;

-- API keys carry durable action permissions so runtime gates can fail closed
-- for enterprise endpoints instead of treating DB-backed keys as implicit admins.
ALTER TABLE api_keys
  ADD COLUMN IF NOT EXISTS action_permissions TEXT[];

-- Endpoint/action-level grants used by the runtime router for enterprise RBAC.
CREATE TABLE IF NOT EXISTS action_permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id),
    grantee_type TEXT NOT NULL CHECK (grantee_type IN ('user', 'role')),
    grantee_id UUID NOT NULL,
    actions TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_action_permissions_grantee
  ON action_permissions(tenant_id, grantee_type, grantee_id);

CREATE INDEX IF NOT EXISTS idx_action_permissions_actions
  ON action_permissions USING GIN(actions);

-- SCIM/IdP groups map to local tenant role names. The runtime permission gate
-- resolves JWT group claims through this mapping before checking role grants.
CREATE TABLE IF NOT EXISTS scim_group_role_mappings (
    tenant_id UUID REFERENCES tenants(id),
    external_group_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    role_names TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (tenant_id, external_group_id)
);

CREATE INDEX IF NOT EXISTS idx_scim_group_role_mappings_roles
  ON scim_group_role_mappings USING GIN(role_names);

-- Runtime sessions are revocable when SCIM deactivates a user. Portal/session
-- implementations can write here while keeping token material out of the table.
CREATE TABLE IF NOT EXISTS user_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    session_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_user_sessions_user_active
  ON user_sessions(user_id, revoked_at)
  WHERE revoked_at IS NULL;

ALTER TABLE user_roles
  DROP CONSTRAINT IF EXISTS user_roles_assignment_source_check;

ALTER TABLE user_roles
  ADD CONSTRAINT user_roles_assignment_source_check
  CHECK (assignment_source IN ('manual', 'sso', 'scim'));

COMMIT;

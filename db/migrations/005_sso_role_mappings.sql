-- 005_sso_role_mappings.sql — SSO IdP group → Platform role mappings
-- Run: PGPASSWORD=admin psql -h localhost -p 54323 -U admin -d mcpfinder -f db/migrations/005_sso_role_mappings.sql

BEGIN;

-- SSO role mappings: when a user logs in via OIDC, their IdP groups/claims
-- are matched against these rows to auto-assign platform roles.
CREATE TABLE IF NOT EXISTS sso_role_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    idp_claim_key TEXT NOT NULL DEFAULT 'groups',  -- JWT claim to inspect, e.g. 'groups', 'roles', 'department'
    idp_claim_value TEXT NOT NULL,                  -- Value to match, e.g. 'engineering', 'finance-team'
    role_id UUID NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tenant_id, idp_claim_key, idp_claim_value, role_id)
);

CREATE INDEX IF NOT EXISTS idx_sso_role_mappings_tenant ON sso_role_mappings(tenant_id);
CREATE INDEX IF NOT EXISTS idx_sso_role_mappings_role ON sso_role_mappings(role_id);

COMMIT;

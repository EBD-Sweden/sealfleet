-- 010_demo_sandbox.sql — fake-data-only external evaluation tenant/workspace.
-- Safe to run repeatedly after auth migrations. Does not create real credentials or secrets.

BEGIN;

INSERT INTO tenants (slug, name, sso_enabled, allowed_domains)
VALUES ('demo-sandbox', 'McpFinder Demo Org', false, ARRAY['mcpfinder.dev'])
ON CONFLICT (slug) DO UPDATE SET
  name = EXCLUDED.name,
  sso_enabled = false,
  allowed_domains = EXCLUDED.allowed_domains,
  updated_at = NOW();

WITH demo_tenant AS (
  SELECT id FROM tenants WHERE slug = 'demo-sandbox'
)
INSERT INTO roles (tenant_id, name, description)
SELECT id, 'demo_viewer', 'Read/execute access to fake demo MCPs and demo_sandbox_invoice_review only.'
FROM demo_tenant
ON CONFLICT (tenant_id, name) DO UPDATE SET
  description = EXCLUDED.description;

-- Placeholder account identity for documentation and audit examples only.
-- Login bootstrap should issue an invite/reset link out-of-band; no reusable secret is seeded here.
WITH demo_tenant AS (
  SELECT id FROM tenants WHERE slug = 'demo-sandbox'
)
INSERT INTO users (tenant_id, email, name, auth_provider, is_active, is_admin)
SELECT id, 'demo.viewer@mcpfinder.dev', 'Demo Sandbox Viewer', 'native', false, false
FROM demo_tenant
ON CONFLICT (email) DO UPDATE SET
  tenant_id = EXCLUDED.tenant_id,
  name = EXCLUDED.name,
  is_active = false,
  is_admin = false,
  updated_at = NOW();

-- Concrete workspace binding lives in runtime/auth metadata: demo callers must present
-- tenant_id=demo-sandbox plus workspace_scope=demo-external-evaluation (JWT claim or X-Workspace-ID).
-- There is no first-class workspaces table in current schema.
COMMENT ON TABLE tenants IS 'Includes demo-sandbox tenant for fake-demo-only external evaluation; runtime/auth workspace_scope=demo-external-evaluation binds the demo workspace; no production data or secrets.';

COMMIT;

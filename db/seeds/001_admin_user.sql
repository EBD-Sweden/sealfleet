-- 001_admin_user.sql — Seed platform tenant + admin user
-- Run: PGPASSWORD=admin psql -h localhost -p 54323 -U admin -d mcpfinder -f db/seeds/001_admin_user.sql

BEGIN;

-- Platform tenant (fixed UUID for easy referencing)
INSERT INTO tenants (id, slug, name)
VALUES ('00000000-0000-0000-0000-000000000001', 'platform', 'McpFinder Platform')
ON CONFLICT (slug) DO NOTHING;

-- Admin user. No password is seeded here; docker-compose sets one from
-- ADMIN_INITIAL_PASSWORD on first boot (only while password_hash IS NULL),
-- otherwise bootstrap via PLATFORM_ADMIN_EMAILS or an external IdP.
INSERT INTO users (tenant_id, email, name, auth_provider, is_admin)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'admin@mcpfinder.io',
    'Admin',
    'native',
    true
)
ON CONFLICT (email) DO NOTHING;

-- Default roles for the platform tenant. The portal derives admin capabilities
-- from user_roles membership (users.is_admin alone grants nothing):
--   admin          -> tenant-scoped administration
--   platform_admin -> cross-tenant administration (Admin → Tenants, IdP config)
INSERT INTO roles (tenant_id, name, description)
VALUES ('00000000-0000-0000-0000-000000000001', 'admin', 'Full administrative access')
ON CONFLICT (tenant_id, name) DO NOTHING;

INSERT INTO roles (tenant_id, name, description)
VALUES ('00000000-0000-0000-0000-000000000001', 'platform_admin', 'Cross-tenant platform administration')
ON CONFLICT (tenant_id, name) DO NOTHING;

-- Link the seeded admin to both roles — without these rows the admin cannot
-- reach Admin → Tenants to configure an IdP (chicken-and-egg on fresh installs).
INSERT INTO user_roles (user_id, role_id)
SELECT u.id, r.id
FROM users u
JOIN roles r ON r.tenant_id = u.tenant_id AND r.name IN ('admin', 'platform_admin')
WHERE u.email = 'admin@mcpfinder.io'
ON CONFLICT (user_id, role_id) DO NOTHING;

COMMIT;

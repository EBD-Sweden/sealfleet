-- 004_auth.sql — Multi-tenant auth: tenants, users, roles, mcp_permissions
-- Run: PGPASSWORD=admin psql -h localhost -p 54323 -U admin -d mcpfinder -f db/migrations/004_auth.sql

BEGIN;

-- Tenants (org-level isolation)
CREATE TABLE IF NOT EXISTS tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    sso_enabled BOOLEAN DEFAULT false,
    oidc_issuer TEXT,
    oidc_client_id TEXT,
    oidc_client_secret TEXT,
    oidc_scopes TEXT DEFAULT 'openid email profile',
    allowed_domains TEXT[],
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Users (portal login accounts)
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id),
    email TEXT UNIQUE NOT NULL,
    name TEXT,
    avatar_url TEXT,
    auth_provider TEXT NOT NULL DEFAULT 'native',  -- 'native' | 'oidc'
    password_hash TEXT,                            -- bcrypt for native
    is_active BOOLEAN DEFAULT true,
    is_admin BOOLEAN DEFAULT false,
    last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Roles (tenant-scoped)
CREATE TABLE IF NOT EXISTS roles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id),
    name TEXT NOT NULL,
    description TEXT,
    UNIQUE (tenant_id, name)
);

-- User ↔ Role many-to-many
CREATE TABLE IF NOT EXISTS user_roles (
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    role_id UUID REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);

-- MCP server-level permissions (grantee = user or role)
CREATE TABLE IF NOT EXISTS mcp_permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id),
    grantee_type TEXT NOT NULL,       -- 'user' or 'role'
    grantee_id UUID NOT NULL,         -- user_id or role_id
    server_id UUID REFERENCES servers(id) ON DELETE CASCADE,
    allowed_tools TEXT[],             -- NULL => all tools on this server
    scopes TEXT[] DEFAULT ARRAY['read'],  -- 'read' | 'write' | 'execute' | 'admin'
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_mcp_perms_grantee ON mcp_permissions(grantee_type, grantee_id);
CREATE INDEX IF NOT EXISTS idx_mcp_perms_server ON mcp_permissions(server_id);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id);

COMMIT;

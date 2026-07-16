-- 001_registry_core.sql — Core registry tables required before auth FKs.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS servers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    server_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    description TEXT DEFAULT '',
    auth_methods JSONB DEFAULT '[]'::jsonb,
    status TEXT DEFAULT 'online',
    registered_at DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW()),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tools (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tool_id TEXT UNIQUE NOT NULL,
    server_id UUID REFERENCES servers(id) ON DELETE CASCADE,
    server_id_str TEXT,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    input_schema JSONB DEFAULT '{}'::jsonb,
    category TEXT DEFAULT '',
    tags JSONB DEFAULT '[]'::jsonb,
    version TEXT DEFAULT '0.1.0',
    registered_at DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW()),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_servers_status ON servers(status);
CREATE INDEX IF NOT EXISTS idx_tools_server_id ON tools(server_id);
CREATE INDEX IF NOT EXISTS idx_tools_server_id_str ON tools(server_id_str);
CREATE INDEX IF NOT EXISTS idx_tools_category ON tools(category);

CREATE TABLE IF NOT EXISTS api_keys (
    api_key TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    is_active BOOLEAN DEFAULT true,
    action_permissions TEXT[],
    allow_identity_delegation BOOLEAN NOT NULL DEFAULT false,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_api_keys_tenant ON api_keys(tenant_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_active ON api_keys(is_active) WHERE is_active = true;

CREATE TABLE IF NOT EXISTS deployments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,
    repo_url TEXT,
    branch TEXT DEFAULT 'main',
    image TEXT,
    endpoint TEXT,
    node_port INT,
    status TEXT DEFAULT 'deploying',
    server_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS audit_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id TEXT,
    tenant_id TEXT NOT NULL DEFAULT 'system',
    user_id TEXT,
    action TEXT NOT NULL,
    resource TEXT,
    server_name TEXT,
    result TEXT,
    trace_id TEXT,
    duration_ms INT DEFAULT 0,
    payload JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_events_created ON audit_events(created_at DESC);

COMMIT;

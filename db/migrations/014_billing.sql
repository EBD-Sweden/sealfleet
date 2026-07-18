-- 014_billing.sql — Self-serve signup + Stripe billing + usage metering.
-- Run: PGPASSWORD=admin psql -h localhost -p 54323 -U admin -d mcpfinder -f db/migrations/014_billing.sql
--
-- Adds:
--   1. api_key_usage_log  — the metering table the router already writes to
--      (_log_api_usage in runtime/router.py) but which no migration created,
--      so usage logging silently no-op'd. This is the Stripe metering feed.
--   2. api_keys.request_count / last_used_at — counters the same router path
--      updates (also previously missing → swallowed by the except: pass).
--   3. subscriptions — one row per tenant tracking its Stripe subscription.

BEGIN;

-- 1. Usage log — exact columns the router INSERTs (router.py _log_api_usage).
CREATE TABLE IF NOT EXISTS api_key_usage_log (
    id BIGSERIAL PRIMARY KEY,
    key_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    tool TEXT,                       -- request path / tool name
    status_code INT,
    response_time_ms INT,
    ip_address TEXT,
    error_msg TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
-- Metering queries aggregate per tenant over a billing window.
CREATE INDEX IF NOT EXISTS idx_usage_tenant_time ON api_key_usage_log(tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_usage_key ON api_key_usage_log(key_id);

-- 2. Per-key counters the router updates on every call.
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS request_count BIGINT NOT NULL DEFAULT 0;
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ;

-- 3. Subscriptions — one per tenant. status mirrors Stripe's subscription
--    status (inactive|trialing|active|past_due|canceled). A tenant is
--    "entitled" (may use the hosted service) when status IN ('trialing','active').
CREATE TABLE IF NOT EXISTS subscriptions (
    tenant_id UUID PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    status TEXT NOT NULL DEFAULT 'inactive',
    plan TEXT,
    seats INT,
    current_period_end TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_customer ON subscriptions(stripe_customer_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);

COMMIT;

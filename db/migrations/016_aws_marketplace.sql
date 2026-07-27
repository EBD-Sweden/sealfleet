-- 016_aws_marketplace.sql — AWS Marketplace SaaS fulfillment.
-- Maps an AWS Marketplace customer (from ResolveCustomer) to a Sealfleet tenant,
-- and tracks subscription status driven by AWS SNS notifications. Access is
-- enforced the same way Stripe billing does it: the tenant's API keys' is_active
-- follows the subscription status.

BEGIN;

CREATE TABLE IF NOT EXISTS aws_marketplace_customers (
    customer_identifier TEXT PRIMARY KEY,          -- AWS Marketplace CustomerIdentifier
    tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL,
    product_code TEXT NOT NULL,
    aws_account_id TEXT,
    -- pending (resolved, not yet registered) | active | unsubscribe_pending | unsubscribed
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_aws_mp_tenant ON aws_marketplace_customers(tenant_id);
CREATE INDEX IF NOT EXISTS idx_aws_mp_status ON aws_marketplace_customers(status);

COMMIT;

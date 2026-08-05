-- 018_aws_marketplace_concurrent_agreements.sql — store the agreement identity.
--
-- Concurrent Agreements: a buyer may hold several simultaneous agreements for
-- one product in a single AWS account, each with its own LicenseArn. Since
-- 2026-06-01 ResolveCustomer no longer returns CustomerIdentifier for new SaaS
-- products, so the LicenseArn is the subscription identity and is what later
-- entitlement checks are filtered by.
--
-- customer_identifier remains the subscription key and now holds the LicenseArn
-- for new agreements, so each concurrent agreement naturally gets its own row.
-- Purely additive: agreements created under the legacy integration keep working.

BEGIN;

ALTER TABLE aws_marketplace_customers
    ADD COLUMN IF NOT EXISTS license_arn             TEXT,
    ADD COLUMN IF NOT EXISTS customer_aws_account_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS aws_mp_customers_license_arn_key
    ON aws_marketplace_customers (license_arn)
    WHERE license_arn IS NOT NULL;

COMMIT;

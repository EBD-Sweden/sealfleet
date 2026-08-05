-- 017_aws_marketplace_selfhost.sql — shift the AWS Marketplace flow from
-- "hosted tenant" to "self-host license".
--
-- Model: AWS Marketplace sells a SELF-HOST license (the customer runs Sealfleet
-- in their OWN infra via the BYOF terraform). We do NOT run anything for them on
-- AWS. On subscribe we RECORD the buyer + their contact details and NOTIFY the
-- operator, who issues a signed license token OFFLINE (private key is never on a
-- server) and sends it to the buyer. This table is the operator's work queue.
--
-- Reuses aws_marketplace_customers from 016; adds contact + issuance fields.
-- status values now mean:
--   pending      — buyer resolved at the fulfillment URL, no contact yet
--   registered   — buyer submitted contact details; awaiting entitlement + issuance
--   active       — AWS confirmed the entitlement (subscribe-success); OK to issue
--   unsubscribe_pending / unsubscribed — subscription ending / ended (revoke/no renew)

BEGIN;

ALTER TABLE aws_marketplace_customers
    ADD COLUMN IF NOT EXISTS org_name          TEXT,
    ADD COLUMN IF NOT EXISTS contact_name       TEXT,
    ADD COLUMN IF NOT EXISTS contact_email      TEXT,
    ADD COLUMN IF NOT EXISTS license_issued     BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS license_issued_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS operator_notified_at TIMESTAMPTZ;

-- Operator queue: entitlements that are active and awaiting a license.
CREATE INDEX IF NOT EXISTS idx_aws_mp_awaiting_license
    ON aws_marketplace_customers(status)
    WHERE license_issued = FALSE;

COMMIT;

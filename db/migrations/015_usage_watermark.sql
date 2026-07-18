-- 015_usage_watermark.sql — track how far usage has been reported to Stripe.
-- The report-usage job aggregates api_key_usage_log per metered tenant and
-- reports the delta as a Stripe meter event; this watermark is the high-water
-- mark so each call is billed exactly once.

BEGIN;

ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS usage_reported_through TIMESTAMPTZ;

COMMIT;

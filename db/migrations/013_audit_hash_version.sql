-- 013_audit_hash_version.sql — mark audit hash payload serialization format
--
-- The /audit/verify endpoint can only excuse unrecoverable JSONB key-order
-- mismatches when a row is explicitly known to predate canonical payload
-- serialization. New writes use canonical-payload-v1 and must fail closed on
-- content mismatches; intentionally backfilled legacy rows may use
-- legacy-json-payload-order.

BEGIN;

ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS audit_hash_version TEXT;

COMMENT ON COLUMN audit_events.audit_hash_version IS 'Audit entry hash payload serialization format: canonical-payload-v1 for current rows; legacy-json-payload-order for explicit pre-canonical backfills.';

COMMIT;

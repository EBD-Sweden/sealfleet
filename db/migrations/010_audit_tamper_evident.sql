-- 010_audit_tamper_evident.sql — make audit_events tamper-evident + append-only
--
-- SOC 2 (CC7.2): audit logs must be tamper-evident and append-only.
-- 1. Add a monotonic sequence and a hash chain (prev_hash -> entry_hash).
--    Each row's entry_hash = sha256(prev_hash || canonical(event fields)); any
--    edit/delete/reorder breaks the chain and is detectable by /audit/verify.
-- 2. Block UPDATE/DELETE at the DB layer with a trigger (fires even for the app
--    role; a superuser disabling it still breaks the hash chain, leaving evidence).

-- Some older deployments created audit_events with a `metadata` column while the
-- writer uses `payload`; ensure `payload` exists so audit writes succeed.
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS payload JSONB;

-- Drop the over-strict result CHECK (success/denied/error) — the writer also
-- emits 'ok'/'rate_limited', so the constraint silently dropped audit events.
ALTER TABLE audit_events DROP CONSTRAINT IF EXISTS audit_events_result_check;

ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS seq BIGSERIAL;
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS prev_hash TEXT;
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS entry_hash TEXT;
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS audit_hash_version TEXT;

CREATE INDEX IF NOT EXISTS idx_audit_seq ON audit_events(seq);

-- Append-only enforcement: reject UPDATE and DELETE.
CREATE OR REPLACE FUNCTION audit_events_block_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only (SOC2 CC7.2); % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_events_no_update ON audit_events;
CREATE TRIGGER trg_audit_events_no_update
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_block_mutation();

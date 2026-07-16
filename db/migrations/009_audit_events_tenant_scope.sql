-- 009_audit_events_tenant_scope.sql — tenant-scoped structured audit events
-- Backfill strategy: existing rows predate tenant-scoped audit persistence, so they
-- are marked as 'system' instead of being attributed to a guessed tenant.

BEGIN;

ALTER TABLE audit_events
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'system';

UPDATE audit_events
   SET tenant_id = 'system'
 WHERE tenant_id IS NULL OR tenant_id = '';

CREATE INDEX IF NOT EXISTS idx_audit_tenant_created
    ON audit_events(tenant_id, created_at DESC);

COMMIT;

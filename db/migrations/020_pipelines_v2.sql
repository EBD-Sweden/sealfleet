-- Durable store for pipelines authored at runtime (portal chat, POST
-- /v2/pipelines/deploy, the MCP control server).
--
-- Shipped example pipelines live in the image. Authored ones were written to
-- PIPELINES_DATA_DIR, which needs a mounted volume; on hosted runtimes with an
-- ephemeral filesystem (Cloud Run, scale-to-zero) they vanished with the
-- instance, and with several replicas each saw a different set. The database
-- is already shared by every replica, so it is the store of record; the data
-- dir remains a dev/offline fallback when no database is configured.

CREATE TABLE IF NOT EXISTS pipelines_v2 (
    name        TEXT PRIMARY KEY,
    definition  JSONB NOT NULL,
    tenant_id   TEXT,
    created_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pipelines_v2_tenant ON pipelines_v2(tenant_id);

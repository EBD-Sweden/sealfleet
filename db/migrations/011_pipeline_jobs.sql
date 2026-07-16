-- 011_pipeline_jobs.sql — async pipeline jobs/workflows tables.
--
-- These were previously created only by historical/manual setup, so a fresh
-- deploy (docker compose / Helm) had no `pipeline_jobs` table and every
-- /jobs request 500'd (jobs + the `workflow` CLI facade were broken out of the
-- box). Schema derived from the router's INSERT/UPDATE/SELECT usage.

CREATE TABLE IF NOT EXISTS pipeline_jobs (
    job_id         TEXT PRIMARY KEY,
    name           TEXT,
    pipeline_name  TEXT,
    status         TEXT NOT NULL DEFAULT 'queued',
    inputs         JSONB,
    result         JSONB,
    error          TEXT,
    tenant_id      TEXT NOT NULL DEFAULT 'system',
    parent_job_id  TEXT,
    created_at     TIMESTAMPTZ DEFAULT now(),
    started_at     TIMESTAMPTZ,
    completed_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_tenant_created
    ON pipeline_jobs(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_parent
    ON pipeline_jobs(parent_job_id);

CREATE TABLE IF NOT EXISTS pipeline_job_steps (
    step_id         TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL,
    step_name       TEXT,
    status          TEXT NOT NULL DEFAULT 'queued',
    inputs          JSONB,
    result          JSONB,
    error           TEXT,
    sequence_order  INT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_pipeline_job_steps_job
    ON pipeline_job_steps(job_id, sequence_order);

-- Tenant-scope runtime-authored pipelines. Review finding: pipelines_v2 rows
-- were loaded, listed, run and deleted globally, so on a multi-tenant hosted
-- deployment one tenant could see, run, and silently overwrite another
-- tenant's authored pipelines (name was the sole primary key).
--
-- tenant_id '' means "global" (legacy rows, single-tenant deployments); the
-- composite key lets two tenants own the same pipeline name independently.

UPDATE pipelines_v2 SET tenant_id = '' WHERE tenant_id IS NULL;
ALTER TABLE pipelines_v2 ALTER COLUMN tenant_id SET DEFAULT '';
ALTER TABLE pipelines_v2 ALTER COLUMN tenant_id SET NOT NULL;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pipelines_v2_pkey'
             AND conrelid = 'pipelines_v2'::regclass
             AND (SELECT count(*) FROM unnest(conkey)) = 1) THEN
    ALTER TABLE pipelines_v2 DROP CONSTRAINT pipelines_v2_pkey;
    ALTER TABLE pipelines_v2 ADD PRIMARY KEY (tenant_id, name);
  END IF;
END $$;

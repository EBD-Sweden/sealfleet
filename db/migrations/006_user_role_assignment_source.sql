-- 006_user_role_assignment_source.sql — distinguish manual and SSO-managed role grants
-- Run: PGPASSWORD=admin psql -h localhost -p 54323 -U admin -d mcpfinder -f db/migrations/006_user_role_assignment_source.sql

BEGIN;

ALTER TABLE user_roles
  ADD COLUMN IF NOT EXISTS assignment_source TEXT NOT NULL DEFAULT 'manual';

ALTER TABLE user_roles
  DROP CONSTRAINT IF EXISTS user_roles_assignment_source_check;

ALTER TABLE user_roles
  ADD CONSTRAINT user_roles_assignment_source_check
  CHECK (assignment_source IN ('manual', 'sso'));

-- Legacy SSO-created grants were indistinguishable before this migration. Treat
-- grants on users whose most recent auth_provider is SSO-backed as SSO-managed so
-- the next SSO login can authoritatively revoke stale IdP group grants. Manual
-- break-glass grants should be re-applied after migration and remain manual.
UPDATE user_roles ur
SET assignment_source = 'sso'
FROM users u
WHERE u.id = ur.user_id
  AND u.auth_provider IN ('google', 'azure', 'oidc');

CREATE INDEX IF NOT EXISTS idx_user_roles_assignment_source
  ON user_roles(user_id, assignment_source);

COMMIT;

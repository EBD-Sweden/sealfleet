# Sealfleet Database Migrations

Run these in order against a fresh PostgreSQL database to set up the full schema.

## Connection
postgresql://admin:admin@localhost:54323/mcpfinder
(production: use env DATABASE_URL)

## Run all migrations
```bash
PGPASSWORD=admin psql -h localhost -p 54323 -U admin -d mcpfinder \
  -f db/migrations/004_auth.sql \
  -f db/migrations/005_sso_role_mappings.sql \
  -f db/migrations/006_user_role_assignment_source.sql \
  -f db/migrations/007_enterprise_rbac_scim.sql \
  -f db/migrations/008_api_key_identity_delegation.sql
```

Or run individually:
```bash
for f in db/migrations/*.sql; do
  PGPASSWORD=admin psql -h localhost -p 54323 -U admin -d mcpfinder -f "$f"
done
```

## Migrations

| File | Description |
|------|-------------|
| 004_auth.sql | tenants, users, roles, user_roles, mcp_permissions — multi-tenant auth + MCP access control |
| 005_sso_role_mappings.sql | sso_role_mappings — maps IdP JWT claims/groups to platform roles |
| 006_user_role_assignment_source.sql | user_roles.assignment_source — distinguishes manual break-glass grants from SSO-managed grants so SSO login can revoke stale IdP roles authoritatively |
| 007_enterprise_rbac_scim.sql | action_permissions, scim_group_role_mappings, user_sessions — endpoint-level RBAC and SCIM lifecycle storage |
| 008_api_key_identity_delegation.sql | api_keys.allow_identity_delegation + metadata — explicit portal delegated identity privilege for sealed handle user binding |

## Seeds

| File | Description |
|------|-------------|
| db/seeds/001_admin_user.sql | Platform tenant + admin@sealfleet.io user (no password seeded; invite/reset or OIDC) + admin role |

## Schema Overview

The tenant-aware login flow relies on these columns in `tenants`:
- `allowed_domains` — email-domain discovery for `/api/sso/start`
- `sso_enabled` — enables the tenant-specific SSO button flow
- `oidc_issuer`, `oidc_client_id`, `oidc_client_secret`, `oidc_scopes` — OIDC discovery + code exchange config

See AUTH_BACKEND.md for full schema documentation.

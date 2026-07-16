# Auth Backend — Multi-tenant User Auth + MCP Permissions

## Overview

The runtime router now supports **two auth methods** that co-exist:

1. **API Key auth** (existing) — `X-API-Key` header or `Authorization: Bearer <api-key>` validated against the `api_keys` table.
2. **User JWT auth** — `Authorization: Bearer <jwt>` signed by the portal with RS256 and validated against the portal JWKS. Legacy HS256 with `NEXTAUTH_SECRET` is disabled by default and only accepted for explicit non-production migrations.

When `REQUIRE_AUTH=true`, the middleware tries API key first, then JWT. If neither is valid → 401.
When `REQUIRE_AUTH=false`, everything passes through as `tenant_id='system'` (unchanged behavior).

## New Tables

### `tenants`
Organization-level isolation. Each tenant has a unique slug, optional OIDC config for SSO.
- Fixed platform tenant: `00000000-0000-0000-0000-000000000001` / slug `platform`

### `users`
Portal login accounts. Linked to a tenant. Supports `native` (bcrypt password) or `oidc` auth.
- Seeded admin: `admin@sealfleet.io` (no password seeded; bootstrap via invite/reset link or OIDC — see `PLATFORM_ADMIN_EMAILS`)

### `roles`
Tenant-scoped named roles (e.g. "admin", "viewer", "operator").

### `user_roles`
Many-to-many join between users and roles.

### `mcp_permissions`
Server-level access grants. A permission row says "this user/role can access this MCP server":
- `grantee_type`: `'user'` or `'role'`
- `grantee_id`: UUID of the user or role
- `server_id`: FK to `servers(id)`
- `allowed_tools`: NULL = all tools, or specific tool names
- `scopes`: `['read']`, `['read','execute']`, etc.
- `expires_at`: optional expiry

## Auth Flow (Middleware)

```
Request → REQUIRE_AUTH=false? → pass (tenant_id='system')
        → X-API-Key or Bearer matches api_keys? → API key auth (auth_type='api_key')
        → Bearer token is valid JWT? → User JWT auth (auth_type='user_jwt')
        → Neither valid → 401 {"error": "Unauthorized"}
```

## Tenant OIDC Code Exchange

The portal's custom `sso` provider now supports tenant-driven OIDC authorization code login from the `/login` page.

Flow summary:
- `/api/sso/start` resolves the tenant from `allowed_domains`, fetches OIDC discovery, and builds the authorization URL.
- `/login/sso/callback` completes `signIn("sso")` with the returned authorization code.
- `src/auth.ts` exchanges the code against the tenant token endpoint and verifies the returned ID token.
- If the IdP advertises `S256`, the portal also passes PKCE `code_verifier` during token exchange.

## JWT Claims

The portal signs JWTs with `NEXTAUTH_RS256_PRIVATE_KEY` (RS256). The router validates portal-issued tokens through `JWKS_URL` (default `${PORTAL_URL}/api/.well-known/jwks.json`). Tokens contain:
```json
{
  "iss": "<PORTAL_JWT_ISSUER or NEXTAUTH_ISSUER>",
  "aud": "<PORTAL_JWT_AUDIENCE or NEXTAUTH_AUDIENCE>",
  "sub": "<user_id UUID>",
  "tenant_id": "<tenant_id UUID>",
  "email": "user@example.com",
  "is_admin": false,
  "exp": 1234567890
}
```

## MCP Permission Enforcement

Enforced by `_enforce_user_mcp_access` for every **user identity** hitting MCP
endpoints (`/call`, `/pipeline`, `/pipelines/{name}/run`, `/v2/pipelines/run`):
- `auth_type='user_jwt'` (portal session tokens), and
- `auth_type='api_key'` **with a delegated identity** (`X-Sealfleet-User-Id` on a
  delegation-enabled key, e.g. the portal's server key). Delegated calls are now
  enforced exactly like direct user calls (defense-in-depth: the router no longer
  trusts the delegating caller to have checked).
- **Pure service API keys** (no delegated user) are NOT checked against
  mcp_permissions — they are scoped by `api_keys.action_permissions`/tenant.

Permission resolution (`_check_user_mcp_permission`):
1. **Platform admin bypass** — JWT `is_admin` claim, or `users.is_admin` in the DB
   (covers delegated identities whose claim never reaches the router)
2. Look up server by name or UUID
3. Check `mcp_permissions` for `grantee_type='user'` + `grantee_id=user_id` + `server_id` (non-expired)
4. If no direct grant → check roles: join `user_roles` → `mcp_permissions` for `grantee_type='role'`
5. If still no grant and the caller presented IdP **group claims** (JWT `groups`
   claim, or `X-Sealfleet-Groups` forwarded by a delegation-enabled caller) →
   resolve groups → roles through BOTH mapping tables
   (`scim_group_role_mappings` and `sso_role_mappings` with
   `idp_claim_key IN ('groups','roles')`) → `mcp_permissions` role grants
6. Any match → allow; no match → 403

**Per-tool enforcement:** every check carries the requested tool. A grant with a
non-empty `allowed_tools` list only covers the listed tools; NULL/empty = whole
MCP. (Previously `allowed_tools` was stored but never enforced.)

### Manifest-declared role gating

An MCP manifest can additionally declare a coarse access gate that is evaluated
before the permission lookup:

```yaml
# runtime/manifests/<mcp>.yaml
access:
  allowed_roles: [trading-ops]     # platform role names
  allowed_groups: [idp-traders]    # raw IdP group claim values
```

User-identity callers must be admin, hold one of the roles (assigned or
group-mapped), or present one of the groups. Self-registration cannot drop a
YAML-declared gate. Pure service API keys are not subject to manifest gating.

## Required Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `REQUIRE_AUTH` | No (default `false`) | Enable auth enforcement |
| `NEXTAUTH_SECRET` | Optional migration fallback | Legacy HS256 fallback secret; ignored for user JWT auth unless `AUTH_ALLOW_LEGACY_PORTAL_HS256=true` in a non-production deployment |
| `AUTH_ALLOW_LEGACY_PORTAL_HS256` | No (non-production migration only) | Explicitly re-enables legacy portal HS256 JWT verification outside production/public-test; never set in production/public-test |
| `ROUTER_RS256_PRIVATE_KEY` | Yes in production/public-test | PKCS8 RSA private key for router-issued MCP access tokens and router JWKS |
| `AUTH_ALLOW_EPHEMERAL_KEYS` | Dev only | Explicitly permits local ephemeral router keys when the deployment env is not production-like |
| `PORTAL_URL` / `JWKS_URL` | Yes when validating portal JWTs | Portal origin or explicit JWKS endpoint for RS256 session-token verification |
| `PORTAL_JWT_ISSUER` / `NEXTAUTH_ISSUER` | Yes in production/public-test | Expected portal session JWT issuer; production-like deployments reject portal JWT validation when omitted |
| `PORTAL_JWT_AUDIENCE` / `NEXTAUTH_AUDIENCE` | Yes in production/public-test | Expected portal session JWT audience; production-like deployments reject portal JWT validation when omitted |
| `MCPFINDER_DEPLOYMENT_ENV` / `DEPLOYMENT_ENV` / `ENVIRONMENT` / `APP_ENV` / `NODE_ENV` | Recommended | Values `production`, `prod`, or `public-test` force persistent router keys and portal JWT issuer/audience config |
| `DATABASE_URL` | No (default `postgresql://admin:***@localhost:54323/mcpfinder`) | PostgreSQL connection |

## Files Changed

- `db/migrations/004_auth.sql` — New tables
- `db/seeds/001_admin_user.sql` — Platform tenant + admin user + admin role
- `runtime/requirements.txt` — Added `PyJWT>=2.8.0`
- `runtime/router.py` — JWT validation, updated middleware, permission checks

# Sealfleet Portal Authentication

## Overview

The portal uses **next-auth v5 (beta)** with a Credentials provider for email/password login. JWTs are used as the session strategy — no server-side session store. The JWT includes tenant and admin context, enabling the backend router to validate tokens for multi-tenant access control.

## How Login Works

1. User visits any portal page → `AppShell` component checks session status via `useSession()`.
2. If unauthenticated, the shell renders children without sidebar/header (login page) or shows a loading state.
3. On `/login`, user enters email + password → calls `signIn("credentials", { ... })`.
4. NextAuth's authorize function:
   - Queries `users` table by email.
   - Checks `is_active = true`.
   - Compares password against `password_hash` using bcrypt.
   - Checks `user_roles` + `roles`; `tenant_admin`/legacy `admin` count only when the role tenant matches the session tenant, while `platform_admin` counts only from a platform-scoped role (`roles.tenant_id IS NULL` or `tenants.slug = 'platform'`) or `PLATFORM_ADMIN_EMAILS`.
   - Returns user object with id, email, name, tenant_id, is_admin, auth_provider.
5. JWT is issued with embedded claims and stored as an httpOnly cookie.
6. Subsequent requests include the JWT automatically.

## Environment Variables

| Variable | Description | Example |
|---|---|---|
| `NEXTAUTH_URL` | Canonical URL of the portal | `http://localhost:3004` |
| `NEXTAUTH_SECRET` / `AUTH_SECRET` | Auth.js secret for cookie/session internals (both set for compatibility) | generate with `openssl rand -base64 32` |
| `NEXTAUTH_RS256_PRIVATE_KEY` | Required in production/public-test. PKCS8 RSA private key used to sign portal JWTs exposed through JWKS. | `-----BEGIN PRIVATE KEY-----...` |
| `AUTH_ALLOW_EPHEMERAL_KEYS` | Development-only escape hatch. Set to `true` only for local/dev without persistent RS256 keys. Ignored in production/public-test. | `true` |
| `MCPFINDER_DEPLOYMENT_ENV` / `DEPLOYMENT_ENV` / `VERCEL_ENV` / `NODE_ENV` | Production-like values (`production`, `prod`, `public-test`) force persistent keys. | `public-test` |
| `PLATFORM_SSO_ALLOWED_EMAILS` | Comma-separated invite allowlist for platform-tenant Google/Azure fallback. | `alice@example.com,bob@example.com` |
| `PLATFORM_SSO_ALLOWED_DOMAINS` | Comma-separated domain allowlist for platform-tenant Google/Azure fallback. | `example.com` |
| `AUTH_ALLOW_DANGEROUS_EMAIL_ACCOUNT_LINKING` | Optional explicit opt-in for Auth.js email account linking across providers. Default is disabled. | `false` |
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://admin:***@localhost:54323/mcpfinder` |

## JWT Payload Shape

The JWT token contains these custom claims (set in `src/auth.ts` callbacks):

```json
{
  "user_id": "uuid",
  "tenant_id": "uuid",
  "is_admin": true,
  "email": "user@example.com",
  "name": "User Name",
  "sub": "uuid",
  "iat": 1234567890,
  "exp": 1234567890
}
```

The session object exposed to client components mirrors this:

```typescript
session.user.id        // string (user UUID)
session.user.email     // string
session.user.name      // string | null
session.user.tenant_id // string (tenant UUID)
session.user.is_admin  // boolean
```

## Protected Paths

| Path | Protection |
|---|---|
| `/login` | Public (no auth required) |
| `/admin/*` | Requires `is_admin = true` in the client guard; every `/api/admin/*` route also re-checks server-side DB roles and tenant scope. |
| `/api/admin/tenants`, `/api/admin/servers` | Server-side `platform_admin` only. |
| `/api/admin/users`, `/api/admin/roles`, `/api/admin/tenants/[id]/sso-mappings` | `platform_admin` can manage all tenants; `tenant_admin` is restricted to its own `tenant_id` and cannot grant/map `platform_admin`. |
| All other paths | Requires authenticated session (AppShell redirect) |
| `/api/auth/*` | NextAuth endpoints (public) |

## Key Files

| File | Purpose |
|---|---|
| `src/auth.ts` | NextAuth configuration, providers, callbacks |
| `src/types/next-auth.d.ts` | TypeScript module augmentation for session/JWT types |
| `src/app/api/auth/[...nextauth]/route.ts` | NextAuth API route handler |
| `src/components/session-provider.tsx` | Client-side SessionProvider wrapper |
| `src/components/app-shell.tsx` | Main layout shell with auth-aware rendering |
| `src/components/auth-guard.tsx` | Route guard (redirects unauth to /login) |
| `src/components/admin-guard.tsx` | Admin-only page guard (shows 403 if not admin) |
| `src/components/user-menu.tsx` | User display + sign-out button |
| `src/lib/admin-auth.ts` | Server-side admin capability resolver for `platform_admin` vs `tenant_admin`, including tenant-scope checks for admin APIs |
| `src/app/login/page.tsx` | Login form |

## Microsoft Azure AD / Entra ID SSO

### Overview

The portal supports Microsoft Azure AD SSO in two modes:

1. **Platform-level Azure OAuth** — A shared multi-tenant Azure App Registration (env vars). Any Azure AD org can sign in.
2. **Per-tenant OIDC** — Each tenant configures their own Azure AD app in the `tenants` DB table with custom issuer, client ID, and secret.

### Platform-Level Setup (Shared Azure App)

#### 1. Register an Azure App

1. Go to [Azure Portal → App Registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade).
2. Click **New registration**.
3. Set **Supported account types** to "Accounts in any organizational directory (Any Microsoft Entra ID tenant – Multitenant)".
4. Set **Redirect URI** (Web):
   ```
   http://localhost:3004/api/auth/callback/azure-ad
   ```
   For production:
   ```
   https://your-domain.com/api/auth/callback/azure-ad
   ```
5. After creation, note the **Application (client) ID** and **Directory (tenant) ID**.

#### 2. Create a Client Secret

1. Go to **Certificates & secrets** → **New client secret**.
2. Copy the secret value immediately (it's only shown once).

#### 3. Configure API Permissions

Required permissions (Microsoft Graph, Delegated):
- `openid`
- `email`
- `profile`
- `User.Read`

Optional (for group-based role mapping):
- `GroupMember.Read.All` (requires admin consent)

Alternatively, configure the app manifest to include groups in the ID token:
```json
"groupMembershipClaims": "SecurityGroup"
```

#### 4. Environment Variables

Set in `portal/.env.local`:
```
AZURE_AD_CLIENT_ID=<your-application-client-id>
AZURE_AD_CLIENT_SECRET=<your-client-secret>
AZURE_AD_TENANT_ID=common
```

- `AZURE_AD_TENANT_ID=common` → any Azure AD org can sign in (multi-tenant).
- Set to a specific tenant GUID to restrict to a single organization.

#### 5. Tenant Domain Matching

When a user signs in via Azure AD, the portal:
1. Extracts the user's email from the Azure AD token.
2. Looks up the email domain in `tenants.allowed_domains`.
3. If no matching tenant, login may fall back to the `platform` tenant only when the email or domain is explicitly listed in `PLATFORM_SSO_ALLOWED_EMAILS` or `PLATFORM_SSO_ALLOWED_DOMAINS`.
4. If the email is not invited/allowlisted and no tenant domain matches, login is rejected.
5. If matched/allowlisted → user is upserted with `auth_provider = 'azure'`, roles are synced from `sso_role_mappings`.

**Important:** You must have a tenant in the DB with the user's email domain in `allowed_domains` for Azure AD login to succeed.

### Per-Tenant OIDC Setup

For organizations that want their own Azure AD app (separate from the platform):

1. The organization registers their own Azure App (same steps as above).
2. Set the redirect URI to the platform's callback URL.
3. In the `tenants` table, set:
   - `sso_enabled = true`
   - `oidc_issuer = 'https://login.microsoftonline.com/{TENANT_GUID}/v2.0'`
   - `oidc_client_id = '<their-app-client-id>'`
   - `oidc_client_secret = '<their-app-client-secret>'`
   - `oidc_scopes = 'openid email profile'`
   - `allowed_domains = ARRAY['their-domain.com']`

Per-tenant OIDC uses the custom SSO credentials provider (id: `sso`) which performs manual OIDC code exchange and token verification. ID-token signatures are verified against the JWKS advertised by the issuer's discovery document (`jwks_uri`), so any spec-compliant IdP works (Keycloak, Okta, Azure AD, Auth0, …).

The redirect URI handed to the IdP is `{NEXTAUTH_URL}/login/sso/callback` (falling back to `AUTH_URL`, then forwarded headers) — register exactly that URL in your IdP's allowed redirect URIs. With the default compose stack that is `http://localhost:3004/login/sso/callback`.

### Per-Tenant OIDC Login Flow

The portal login page now drives tenant-aware OIDC directly:

1. User enters their work email on `/login`.
2. Clicking **Continue with your organization** sends the email to `POST /api/sso/start`.
3. The portal matches the email domain against `tenants.allowed_domains`, fetches the tenant's OIDC discovery document, and builds the authorization URL.
4. If the IdP supports `S256`, the portal adds PKCE (`code_challenge` on redirect, `code_verifier` on token exchange).
5. The IdP redirects back to `/login/sso/callback`.
6. The callback page finishes `signIn("sso")`, which exchanges the authorization code, verifies the ID token, and derives the authoritative user email from verified OIDC claims (`email`, or email-shaped `preferred_username`/`upn` fallback). If `email_verified=false`, the email is missing, or the claim email differs from the submitted email, login is rejected before any user upsert or SSO role sync.
7. The portal re-resolves the tenant from the verified claim email and only upserts/syncs roles when it matches the tenant whose issuer verified the token.

### Group-Based Role Mapping

Azure AD can include group membership in the ID token. The portal reads the `groups` claim and maps it to tenant-scoped roles via the `sso_role_mappings` table. SSO login is authoritative for SSO-managed grants: on every SSO login the portal deletes the user's `assignment_source = 'sso'` rows and inserts only the currently matched mappings. If no mappings match, stale SSO grants are removed. Manual/break-glass grants stay `assignment_source = 'manual'` and are not removed by SSO sync.

SSO mappings cannot grant `platform_admin`; platform-admin grants require `PLATFORM_ADMIN_EMAILS` or an explicit manual platform-scoped `platform_admin` role (`roles.tenant_id IS NULL` or tenant slug `platform`) assigned by an existing platform admin. Tenant-scoped `platform_admin` role rows are ignored by server-side admin resolution.

```sql
INSERT INTO sso_role_mappings (tenant_id, idp_claim_key, idp_claim_value, role_id)
VALUES (
  '<tenant-uuid>',
  'groups',
  '<azure-ad-group-object-id>',
  '<platform-role-uuid>'
);
```

### Platform Admin Emails

Set `PLATFORM_ADMIN_EMAILS` env var (comma-separated) to auto-grant platform-admin status to specific emails regardless of tenant role mappings. This is distinct from `tenant_admin`: platform admins may list/mutate tenants and shared server metadata; tenant admins are restricted to their own tenant's users, roles, and SSO mappings.
```
PLATFORM_ADMIN_EMAILS=admin@example.com,cto@example.com
```

## Backend Integration

The backend router (`runtime/router.py`) validates portal session JWTs as RS256 tokens against the portal JWKS (`JWKS_URL`, default `${PORTAL_URL}/api/.well-known/jwks.json`) or a configured portal public key cache (`PORTAL_RS256_PUBLIC_KEY` or `PORTAL_JWT_PUBLIC_KEY`, optionally keyed by `PORTAL_RS256_KEY_ID`/`PORTAL_JWT_KEY_ID`). Set `PORTAL_JWT_ISSUER`/`NEXTAUTH_ISSUER` and `PORTAL_JWT_AUDIENCE`/`NEXTAUTH_AUDIENCE` in production/public-test so issuer and audience are enforced fail-closed; when audience is configured, tokens missing `aud` are rejected. The token includes `tenant_id` for row-level security and `is_admin` for admin-only endpoints. Legacy HS256 validation with `NEXTAUTH_SECRET` remains only as a migration fallback, applies the same issuer/audience checks, and should not be used for new production deployments.

The public `/token` token-exchange endpoint rate-limits by the direct client socket IP and subject-token hash. It ignores `X-Forwarded-For` by default so callers cannot spoof the rate-limit source. If the router is deployed behind a known reverse proxy or load balancer, set `TRUSTED_PROXY_CIDRS` to a comma-separated allowlist of proxy IPs/CIDRs; only requests whose direct peer is in that allowlist may supply the original client IP via `X-Forwarded-For`.

Example:

```bash
TRUSTED_PROXY_CIDRS=10.0.0.0/8,192.168.1.10/32
TOKEN_EXCHANGE_RATE_LIMIT_MAX=60
TOKEN_EXCHANGE_RATE_LIMIT_WINDOW_SECONDS=60
```

## Production/public-test RS256 key generation and rotation

Generate independent persistent RSA keys for the portal and router. Do not reuse the same private key across components.

```bash
# Portal JWT signing key (PKCS8 PEM)
openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out portal-rs256-private.pem
export NEXTAUTH_RS256_PRIVATE_KEY="$(cat portal-rs256-private.pem)"

# Router token-exchange signing key (PKCS8 PEM)
openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out router-rs256-private.pem
export ROUTER_RS256_PRIVATE_KEY="$(cat router-rs256-private.pem)"
```

Rotation procedure:
1. Generate a new keypair in the secret manager.
2. Deploy the new private key to the signing service (portal for session JWTs, router for MCP access tokens). The `kid` is derived from the public key and changes automatically.
3. Keep old instances running only until all old JWTs expire, or shorten token TTL before rotation.
4. Verify `/api/.well-known/jwks.json` on the portal and `/.well-known/jwks.json` on the router expose the expected new `kid`.
5. Remove the old private key from secrets after the maximum JWT lifetime has elapsed.

Production-like environments (`NODE_ENV`, `DEPLOYMENT_ENV`, or `MCPFINDER_DEPLOYMENT_ENV` set to `production`, `prod`, or `public-test`; portal deployments may also use `VERCEL_ENV`) refuse to start or sign tokens without persistent keys. Ephemeral keys are allowed only for local development with `AUTH_ALLOW_EPHEMERAL_KEYS=true`.

## Google Workspace / Google OAuth SSO

### Overview

The portal supports Google OAuth in two modes:

1. **Platform-level Google OAuth** — A shared Google OAuth client (env vars). Any Google account can sign in, matched to tenant by email domain.
2. **Per-tenant Google OIDC** — Each tenant configures their own Google OAuth app in the `tenants` DB table (for Google Workspace with HD restriction).

### Platform-Level Setup (Shared Google App)

#### 1. Create Google OAuth Credentials

1. Go to [Google Cloud Console → APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials).
2. Click **Create Credentials** → **OAuth client ID**.
3. Set **Application type** to "Web application".
4. Add **Authorized redirect URIs**:
   ```
   http://localhost:3004/api/auth/callback/google
   ```
   For production:
   ```
   https://your-domain.com/api/auth/callback/google
   ```
5. Copy the **Client ID** and **Client Secret**.

#### 2. Configure OAuth Consent Screen

1. Go to **OAuth consent screen**.
2. Set **User type** to "External" (or "Internal" for Google Workspace orgs).
3. Add scopes: `openid`, `email`, `profile`.
4. Add test users during development if consent screen is in "Testing" mode.

#### 3. Environment Variables

Set in `portal/.env.local`:
```
GOOGLE_CLIENT_ID=<your-google-client-id>
GOOGLE_CLIENT_SECRET=<your-google-client-secret>
PLATFORM_ADMIN_EMAILS=admin@sealfleet.io
```

#### 4. Callback URL

The Google OAuth callback URL is:
```
http://localhost:3004/api/auth/callback/google
```
Production:
```
https://your-domain.com/api/auth/callback/google
```

This must match exactly in the Google Cloud Console redirect URI configuration.

### Tenant Domain Matching

When a user signs in via Google:
1. The portal extracts the user's email from the Google ID token.
2. Verifies `email_verified = true`.
3. Looks up the email domain in `tenants.allowed_domains`.
4. If no matching tenant, the portal may fall back to the `platform` tenant only when the email or domain is explicitly listed in `PLATFORM_SSO_ALLOWED_EMAILS` or `PLATFORM_SSO_ALLOWED_DOMAINS`.
5. If no tenant matches and the email/domain is not allowlisted → login is rejected.
6. User is upserted with `auth_provider = 'google'`, roles synced from `sso_role_mappings`.

### Google Workspace HD Claim

Google includes the `hd` (hosted domain) claim for Google Workspace accounts. This is available in SSO role mappings:

```sql
INSERT INTO sso_role_mappings (tenant_id, idp_claim_key, idp_claim_value, role_id)
VALUES (
  '<tenant-uuid>',
  'hd',
  'acme.com',
  '<platform-role-uuid>'
);
```

### Per-Tenant Google OIDC

For organizations wanting their own Google OAuth app:

1. Create a separate Google OAuth client (same steps as above).
2. Set the redirect URI to the platform's callback URL.
3. In the `tenants` table, set:
   - `sso_enabled = true`
   - `oidc_issuer = 'https://accounts.google.com'`
   - `oidc_client_id = '<their-google-client-id>'`
   - `oidc_client_secret = '<their-google-client-secret>'`
   - `oidc_scopes = 'openid email profile'`
   - `allowed_domains = ARRAY['their-domain.com']`

## Auth Implementation Status (2026-03-29)

### Working
- ✅ Native login (email/password with bcrypt)
- ✅ Google Workspace SSO (requires GOOGLE_CLIENT_ID/SECRET env vars)
- ✅ Microsoft Azure AD SSO (requires AZURE_AD_CLIENT_ID/SECRET env vars)
- ✅ Per-tenant dynamic OIDC from the login page (`/api/sso/start` → `/login/sso/callback`)
- ✅ SSO role mappings (IdP claims → tenant-scoped roles via sso_role_mappings table)
- ✅ Authoritative SSO role revocation for SSO-managed grants (`user_roles.assignment_source = 'sso'`)
- ✅ Split admin authorization: `platform_admin` for platform CRUD/shared resources, `tenant_admin` limited to own tenant
- ✅ is_active check enforced for all auth providers (SSO + native)
- ✅ All DB queries wrapped in try/catch with server-side logging

### TODO
- ⏳ Token refresh / session expiry handling
- ⏳ Account linking (same email, different providers) — `allowDangerousEmailAccountLinking` is set on Azure AD; needs security review
- ⏳ MFA support
- ⏳ Rate limiting on credentials login (brute-force protection)

import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import Google from "next-auth/providers/google";
import type { GoogleProfile } from "next-auth/providers/google";
import MicrosoftEntraId from "next-auth/providers/microsoft-entra-id";
import { Pool } from "pg";
import bcrypt from "bcryptjs";
import * as jose from "jose";
import {
  getSigningKey,
  getVerificationKey,
  getKid,
  SIGNING_ALG,
} from "@/lib/auth-keys";
import {
  isDangerousEmailAccountLinkingAllowed,
  isPlatformAdminEmail,
  isPlatformSsoEmailAllowed,
} from "@/lib/auth-policy";

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
});

// ─── Type declarations ───────────────────────────────────────────────

declare module "next-auth" {
  interface User {
    tenant_id: string;
    is_admin: boolean;
    auth_provider: string;
  }
}

declare module "@auth/core/jwt" {
  interface JWT {
    user_id: string;
    tenant_id: string;
    is_admin: boolean;
    email: string;
  }
}

// ─── Tenant types ────────────────────────────────────────────────────

interface TenantRow {
  id: string;
  slug: string;
  name: string;
  sso_enabled: boolean;
  oidc_issuer: string | null;
  oidc_client_id: string | null;
  oidc_client_secret: string | null;
  oidc_scopes: string | null;
  allowed_domains: string[];
}

interface SsoRoleMappingRow {
  id: string;
  tenant_id: string;
  idp_claim_key: string;
  idp_claim_value: string;
  role_id: string;
}

interface AdminRoleRow {
  name: string;
  tenant_id: string | null;
  tenant_slug: string | null;
}

function isTenantScopedAdminRole(role: AdminRoleRow, tenantId: string): boolean {
  const roleName = role.name.trim().toLowerCase();
  return (
    role.tenant_id === tenantId &&
    (roleName === "admin" || roleName === "tenant_admin")
  );
}

function isPlatformAdminRole(role: AdminRoleRow): boolean {
  const roleName = role.name.trim().toLowerCase();
  return (
    roleName === "platform_admin" &&
    (role.tenant_id === null || role.tenant_slug === "platform")
  );
}

function normalizeEmail(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const email = value.trim().toLowerCase();
  return email.includes("@") ? email : null;
}

function getVerifiedOidcEmail(claims: Record<string, unknown>): string | null {
  if (claims.email_verified === false) return null;
  return (
    normalizeEmail(claims.email) ??
    normalizeEmail(claims.preferred_username) ??
    normalizeEmail(claims.upn)
  );
}

// ─── Tenant lookup by email domain ──────────────────────────────────

/**
 * Find a tenant whose `allowed_domains` array contains the email's domain.
 * Returns null on no match or on DB error (logged server-side).
 */
export async function getTenantByDomain(email: string): Promise<TenantRow | null> {
  const domain = email.split("@")[1]?.toLowerCase();
  if (!domain) return null;

  try {
    const result = await pool.query<TenantRow>(
      `SELECT id, slug, name, sso_enabled, oidc_issuer, oidc_client_id,
              oidc_client_secret, oidc_scopes, allowed_domains
       FROM tenants
       WHERE allowed_domains @> ARRAY[$1]::text[]
       LIMIT 1`,
      [domain]
    );
    return result.rows[0] ?? null;
  } catch (err) {
    console.error("[Auth] getTenantByDomain failed for domain:", domain, err);
    return null;
  }
}

/**
 * Get the default "platform" tenant (fallback for unmatched domains).
 */
async function getPlatformTenant(): Promise<TenantRow | null> {
  try {
    const result = await pool.query<TenantRow>(
      `SELECT id, slug, name, sso_enabled, oidc_issuer, oidc_client_id,
              oidc_client_secret, oidc_scopes, allowed_domains
       FROM tenants
       WHERE slug = 'platform'
       LIMIT 1`
    );
    return result.rows[0] ?? null;
  } catch (err) {
    console.error("[Auth] getPlatformTenant query failed:", err);
    return null;
  }
}

// ─── SSO role mapping engine ────────────────────────────────────────

/**
 * Given a tenant and OIDC claims from the IdP token, resolve which platform
 * role_ids should be assigned based on sso_role_mappings rows.
 *
 * Handles both array claims (e.g. groups: ["engineering", "devs"]) and
 * scalar string claims (e.g. department: "engineering").
 */
export async function applySSORoleMappings(
  tenantId: string,
  oidcClaims: Record<string, unknown>
): Promise<string[]> {
  try {
    const result = await pool.query<SsoRoleMappingRow>(
      `SELECT srm.id, srm.tenant_id, srm.idp_claim_key, srm.idp_claim_value, srm.role_id
       FROM sso_role_mappings srm
       JOIN roles r ON r.id = srm.role_id
       WHERE srm.tenant_id = $1
         AND r.tenant_id = $1
         AND LOWER(r.name) <> 'platform_admin'`,
      [tenantId]
    );

    const matchedRoleIds: string[] = [];

    for (const mapping of result.rows) {
      const claimValue = oidcClaims[mapping.idp_claim_key];
      if (claimValue === undefined || claimValue === null) continue;

      let matched = false;
      if (Array.isArray(claimValue)) {
        matched = claimValue.some(
          (v) => String(v).toLowerCase() === mapping.idp_claim_value.toLowerCase()
        );
      } else if (typeof claimValue === "string") {
        matched = claimValue.toLowerCase() === mapping.idp_claim_value.toLowerCase();
      }

      if (matched) {
        matchedRoleIds.push(mapping.role_id);
      }
    }

    return [...new Set(matchedRoleIds)];
  } catch (err) {
    console.error("[Auth] applySSORoleMappings failed for tenant:", tenantId, err);
    return [];
  }
}

// ─── Consolidated SSO user upsert + role sync ───────────────────────

/**
 * Upsert a user from any SSO provider (Google, Azure, custom OIDC).
 * Assigns roles from SSO mappings, checks admin status, verifies is_active.
 * Returns null if the user is inactive or on DB error.
 */
export async function upsertSsoUser(params: {
  email: string;
  name: string | null;
  avatarUrl: string | null;
  tenantId: string;
  authProvider: "google" | "azure" | "oidc";
  oidcClaims: Record<string, unknown>;
}): Promise<{
  id: string;
  email: string;
  name: string | null;
  tenant_id: string;
  is_admin: boolean;
  auth_provider: string;
} | null> {
  const { email, name, avatarUrl, tenantId, authProvider, oidcClaims } = params;

  try {
    // Upsert user — new users get is_active=true; existing users keep their current is_active
    const upsertResult = await pool.query(
      `INSERT INTO users (email, name, avatar_url, tenant_id, auth_provider, is_active)
       VALUES ($1, $2, $3, $4, $5, true)
       ON CONFLICT (email) DO UPDATE SET
         name = COALESCE(EXCLUDED.name, users.name),
         avatar_url = COALESCE(EXCLUDED.avatar_url, users.avatar_url),
         auth_provider = $5,
         last_login_at = NOW()
       WHERE users.tenant_id = EXCLUDED.tenant_id
       RETURNING id, email, name, tenant_id, is_active`,
      [email, name, avatarUrl, tenantId, authProvider]
    );

    if (upsertResult.rows.length === 0) {
      console.error("[Auth] SSO login denied — existing email belongs to another tenant:", email);
      return null;
    }

    const user = upsertResult.rows[0];

    if (user.tenant_id !== tenantId) {
      console.error("[Auth] SSO login denied — existing email belongs to another tenant:", email);
      return null;
    }

    // Check is_active — deactivated users must not log in via SSO
    if (!user.is_active) {
      console.error("[Auth] SSO login denied — user is deactivated:", email);
      return null;
    }

    // Resolve role mappings from SSO claims
    const roleIds = await applySSORoleMappings(tenantId, oidcClaims);

    // Sync user_roles: SSO is authoritative for SSO-managed grants on every SSO login.
    await pool.query(
      `DELETE FROM user_roles WHERE user_id = $1 AND assignment_source = 'sso'`,
      [user.id]
    );
    for (const roleId of roleIds) {
      await pool.query(
        `INSERT INTO user_roles (user_id, role_id, assignment_source) VALUES ($1, $2, 'sso')
         ON CONFLICT (user_id, role_id) DO NOTHING`,
        [user.id, roleId]
      );
    }

    // Check admin: tenant_admin/admin grants only within user's tenant; platform_admin must be platform-scoped.
    const roleResult = await pool.query<AdminRoleRow>(
      `SELECT r.name, r.tenant_id, t.slug AS tenant_slug FROM roles r
       JOIN user_roles ur ON ur.role_id = r.id
       LEFT JOIN tenants t ON t.id = r.tenant_id
       WHERE ur.user_id = $1
         AND r.name IN ('admin', 'tenant_admin', 'platform_admin')`,
      [user.id]
    );
    const hasTenantAdminRole = roleResult.rows.some((row) =>
      isTenantScopedAdminRole(row, user.tenant_id)
    );
    const hasPlatformAdminRole = roleResult.rows.some((row) => isPlatformAdminRole(row));
    const isAdmin =
      hasTenantAdminRole ||
      hasPlatformAdminRole ||
      isPlatformAdminEmail(email);

    return {
      id: user.id,
      email: user.email,
      name: user.name,
      tenant_id: user.tenant_id,
      is_admin: isAdmin,
      auth_provider: authProvider,
    };
  } catch (err) {
    console.error("[Auth] upsertSsoUser failed for:", email, "provider:", authProvider, err);
    return null;
  }
}

// ─── OIDC token verification ────────────────────────────────────────

async function verifyOidcToken(
  tenant: TenantRow,
  idToken: string
): Promise<Record<string, unknown> | null> {
  if (!tenant.oidc_issuer) return null;

  try {
    const issuer = tenant.oidc_issuer.replace(/\/$/, "");
    // Resolve the JWKS location from the OIDC discovery document (jwks_uri).
    // IdPs place it in different spots (Keycloak: /protocol/openid-connect/certs,
    // Okta: /v1/keys, Azure AD: login.microsoftonline.com/.../keys) — only
    // Auth0-style IdPs happen to use {issuer}/.well-known/jwks.json.
    let jwksUri = `${issuer}/.well-known/jwks.json`;
    const discoveryRes = await fetch(
      `${issuer}/.well-known/openid-configuration`,
      { cache: "no-store" }
    ).catch(() => null);
    if (discoveryRes?.ok) {
      const discovery = (await discoveryRes.json()) as { jwks_uri?: string };
      if (discovery.jwks_uri) jwksUri = discovery.jwks_uri;
    }
    const jwks = jose.createRemoteJWKSet(new URL(jwksUri));

    const { payload } = await jose.jwtVerify(idToken, jwks, {
      // Match the token's iss claim against the normalized issuer (IdPs do not
      // emit trailing slashes even when the tenant config includes one).
      issuer: [issuer, tenant.oidc_issuer],
      audience: tenant.oidc_client_id ?? undefined,
    });

    return payload as Record<string, unknown>;
  } catch (err) {
    console.error("[OIDC] Token verification failed:", err);
    return null;
  }
}

async function exchangeOidcCode(
  tenant: TenantRow,
  code: string,
  redirectUri: string,
  codeVerifier?: string
): Promise<string | null> {
  if (!tenant.oidc_issuer || !tenant.oidc_client_id || !tenant.oidc_client_secret) {
    return null;
  }

  try {
    const issuer = tenant.oidc_issuer.replace(/\/$/, "");
    const configRes = await fetch(`${issuer}/.well-known/openid-configuration`);
    if (!configRes.ok) return null;
    const config = (await configRes.json()) as { token_endpoint: string };

    const body = new URLSearchParams({
      grant_type: "authorization_code",
      code,
      redirect_uri: redirectUri,
      client_id: tenant.oidc_client_id,
      client_secret: tenant.oidc_client_secret,
    });

    if (codeVerifier) {
      body.set("code_verifier", codeVerifier);
    }

    const tokenRes = await fetch(config.token_endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
    });

    if (!tokenRes.ok) {
      console.error("[OIDC] Token exchange failed:", await tokenRes.text());
      return null;
    }

    const tokens = (await tokenRes.json()) as { id_token?: string };
    return tokens.id_token ?? null;
  } catch (err) {
    console.error("[OIDC] Code exchange failed:", err);
    return null;
  }
}

// ─── NextAuth configuration ─────────────────────────────────────────

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [
    // ── Standard email/password login ──
    Credentials({
      id: "credentials",
      name: "Credentials",
      credentials: {
        email: { label: "Username or email", type: "text" },
        password: { label: "Password", type: "password" },
      },
      async authorize(credentials) {
        if (!credentials?.email || !credentials?.password) {
          return null;
        }

        const identifier = (credentials.email as string).trim();
        const password = credentials.password as string;

        try {
          const result = await pool.query(
            `SELECT id, email, name, password_hash, tenant_id, is_active, auth_provider
             FROM users
             WHERE lower(email) = lower($1)
                OR lower(name) = lower($1)
                OR lower(email) = lower($2)
             ORDER BY CASE WHEN lower(email) = lower($1) THEN 0 WHEN lower(name) = lower($1) THEN 1 ELSE 2 END
             LIMIT 1`,
            [identifier, `${identifier}@sealfleet.io`]
          );

          if (result.rows.length === 0) {
            return null;
          }

          const user = result.rows[0];

          if (!user.is_active) {
            return null;
          }

          const passwordValid = await bcrypt.compare(password, user.password_hash);
          if (!passwordValid) {
            return null;
          }

          const roleResult = await pool.query<AdminRoleRow>(
            `SELECT r.name, r.tenant_id, t.slug AS tenant_slug FROM roles r
             JOIN user_roles ur ON ur.role_id = r.id
             LEFT JOIN tenants t ON t.id = r.tenant_id
             WHERE ur.user_id = $1
               AND r.name IN ('admin', 'tenant_admin', 'platform_admin')`,
            [user.id]
          );
          const hasTenantAdminRole = roleResult.rows.some((row) =>
            isTenantScopedAdminRole(row, user.tenant_id)
          );
          const hasPlatformAdminRole = roleResult.rows.some((row) => isPlatformAdminRole(row));
          const isAdmin =
            hasTenantAdminRole ||
            hasPlatformAdminRole ||
            isPlatformAdminEmail(user.email);

          return {
            id: user.id,
            email: user.email,
            name: user.name,
            tenant_id: user.tenant_id,
            is_admin: isAdmin,
            auth_provider: user.auth_provider || "credentials",
          };
        } catch (err) {
          console.error("[Auth] Credentials authorize failed for:", identifier, err);
          return null;
        }
      },
    }),

    // ── Google OIDC provider (platform-level) ──
    // Uses GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET from env.
    // Tenant resolution happens in the signIn callback below.
    Google({
      clientId: process.env.GOOGLE_CLIENT_ID ?? "",
      clientSecret: process.env.GOOGLE_CLIENT_SECRET ?? "",
      authorization: {
        params: {
          prompt: "select_account",
          // Request the hd (hosted domain) claim for Workspace detection
          hd: "*",
        },
      },
      // Map Google profile → next-auth User shape
      profile(profile: GoogleProfile) {
        return {
          id: profile.sub,
          email: profile.email,
          name: profile.name,
          image: profile.picture,
          // Defaults — real values set in signIn callback and stored in JWT
          tenant_id: "",
          is_admin: false,
          auth_provider: "google",
        };
      },
    }),

    // ── SSO/OIDC login (tenant-aware, dynamic) ──
    // Custom Credentials provider for enterprise OIDC (non-Google IdPs).
    // NOTE: This requires a frontend flow to handle the OIDC redirect and pass
    // the authorization code or id_token. See AUTH_PORTAL.md "Per-Tenant OIDC".
    Credentials({
      id: "sso",
      name: "SSO",
      credentials: {
        email: { label: "Email", type: "email" },
        code: { label: "Authorization Code", type: "text" },
        id_token: { label: "ID Token", type: "text" },
        redirect_uri: { label: "Redirect URI", type: "text" },
        code_verifier: { label: "Code Verifier", type: "text" },
      },
      async authorize(credentials) {
        if (!credentials?.email) return null;

        const submittedEmail = normalizeEmail(credentials.email);
        if (!submittedEmail) return null;

        try {
          // The submitted email only selects the candidate tenant/issuer needed
          // to verify the OIDC token. The token's verified subject email remains
          // authoritative for tenant binding and user upsert below.
          const tenant = await getTenantByDomain(submittedEmail);
          if (!tenant || !tenant.sso_enabled) {
            console.error("[SSO] No SSO-enabled tenant for email:", submittedEmail);
            return null;
          }

          let claims: Record<string, unknown> | null = null;

          if (credentials.id_token) {
            claims = await verifyOidcToken(tenant, credentials.id_token as string);
          } else if (credentials.code && credentials.redirect_uri) {
            const idToken = await exchangeOidcCode(
              tenant,
              credentials.code as string,
              credentials.redirect_uri as string,
              (credentials.code_verifier as string | undefined) ?? undefined
            );
            if (idToken) {
              claims = await verifyOidcToken(tenant, idToken);
            }
          }

          if (!claims) {
            console.error("[SSO] Failed to obtain valid OIDC claims for:", submittedEmail);
            return null;
          }

          const verifiedEmail = getVerifiedOidcEmail(claims);
          if (!verifiedEmail || verifiedEmail !== submittedEmail) {
            console.error("[SSO] Email mismatch or unverified email claim:", verifiedEmail, "vs", submittedEmail);
            return null;
          }

          const verifiedTenant = await getTenantByDomain(verifiedEmail);
          if (!verifiedTenant || !verifiedTenant.sso_enabled || verifiedTenant.id !== tenant.id) {
            console.error("[SSO] Verified email resolved to unexpected tenant:", verifiedEmail);
            return null;
          }

          const user = await upsertSsoUser({
            email: verifiedEmail,
            name: (claims.name as string) ?? (claims.preferred_username as string) ?? null,
            avatarUrl: (claims.picture as string) ?? null,
            tenantId: verifiedTenant.id,
            authProvider: "oidc",
            oidcClaims: claims,
          });

          // Carry the IdP group claims into the session JWT so the router can
          // apply group->role mappings at request time (defense-in-depth on
          // top of the login-time role materialization above).
          if (user && Array.isArray(claims.groups)) {
            (user as { groups?: string[] }).groups = (claims.groups as unknown[]).map(String);
          }

          return user;
        } catch (err) {
          console.error("[SSO] authorize failed for:", submittedEmail, err);
          return null;
        }
      },
    }),

    // ── Microsoft Azure AD / Entra ID (platform-level multi-tenant) ──
    // Uses AZURE_AD_CLIENT_ID / AZURE_AD_CLIENT_SECRET / AZURE_AD_TENANT_ID from env.
    // Per-tenant OIDC with custom issuer/client uses the SSO credentials provider above.
    ...(process.env.AZURE_AD_CLIENT_ID
      ? [
          MicrosoftEntraId({
            id: "azure-ad",
            name: "Microsoft",
            clientId: process.env.AZURE_AD_CLIENT_ID,
            clientSecret: process.env.AZURE_AD_CLIENT_SECRET ?? "",
            issuer: `https://login.microsoftonline.com/${process.env.AZURE_AD_TENANT_ID ?? "common"}/v2.0`,
            authorization: {
              params: {
                scope: "openid email profile User.Read",
              },
            },
            // Email-account linking is fail-closed by default. Only enable this
            // after the deployment has explicitly accepted that every enabled
            // issuer verifies email ownership and has a trusted issuer boundary.
            allowDangerousEmailAccountLinking: isDangerousEmailAccountLinkingAllowed(),
          }),
        ]
      : []),
  ],
  session: {
    strategy: "jwt",
  },
  // ── JWT signing override (RS256 + JWKS) ──────────────────────────
  // We replace next-auth's default A256CBC-HS512 JWE with an RS256 JWS so
  // backend services can verify our session tokens against a public JWKS at
  // `${NEXTAUTH_URL}/api/.well-known/jwks.json` — no shared secret required.
  // Token payload shape (user_id, tenant_id, is_admin, email, sub, exp, iat)
  // is preserved; only the algorithm and signing key change.
  jwt: {
    async encode({ token, maxAge }) {
      const now = Math.floor(Date.now() / 1000);
      // next-auth defaults maxAge to 30 days when not supplied
      const ttl = typeof maxAge === "number" ? maxAge : 30 * 24 * 60 * 60;

      const key = await getSigningKey();
      const kid = await getKid();

      const payload = { ...(token ?? {}) } as jose.JWTPayload;

      const signer = new jose.SignJWT(payload)
        .setProtectedHeader({ alg: SIGNING_ALG, kid, typ: "JWT" })
        .setIssuedAt(now)
        .setExpirationTime(now + ttl);

      const issuer = process.env.PORTAL_JWT_ISSUER || process.env.NEXTAUTH_ISSUER;
      if (issuer) signer.setIssuer(issuer);
      const audience = process.env.PORTAL_JWT_AUDIENCE || process.env.NEXTAUTH_AUDIENCE;
      if (audience) signer.setAudience(audience);

      // Preserve `sub` if the caller already set one in the token, otherwise
      // fall back to user_id (matches the existing payload shape).
      const sub =
        (token as { sub?: string; user_id?: string } | undefined)?.sub ??
        (token as { user_id?: string } | undefined)?.user_id;
      if (sub) signer.setSubject(sub);

      return await signer.sign(key);
    },
    async decode({ token }) {
      if (!token) return null;
      try {
        const key = await getVerificationKey();
        const { payload } = await jose.jwtVerify(token, key, {
          algorithms: [SIGNING_ALG],
        });
        return payload as unknown as import("@auth/core/jwt").JWT;
      } catch (err) {
        console.error("[Auth] JWT verification failed:", err);
        return null;
      }
    },
  },
  pages: {
    signIn: "/login",
  },
  callbacks: {
    // ── signIn callback: tenant-aware Google + Azure AD login ──
    async signIn({ user, account, profile }) {
      // ── Google provider ──
      if (account?.provider === "google") {
        try {
          const googleProfile = profile as GoogleProfile | undefined;
          if (!googleProfile?.email) {
            console.error("[Google] signIn denied — no email in profile");
            return false;
          }

          const email = googleProfile.email.toLowerCase();

          if (!googleProfile.email_verified) {
            console.error("[Google] Email not verified:", email);
            return false;
          }

          let tenant = await getTenantByDomain(email);
          if (!tenant) {
            if (!isPlatformSsoEmailAllowed(email)) {
              console.error("[SSO] Platform tenant fallback denied by invite/domain allowlist:", email);
              return false;
            }
            tenant = await getPlatformTenant();
          }
          if (!tenant) {
            console.error("[Google] No tenant found for:", email);
            return false;
          }

          const oidcClaims: Record<string, unknown> = {
            email: googleProfile.email,
            hd: googleProfile.hd ?? null,
            sub: googleProfile.sub,
            name: googleProfile.name,
          };

          const dbUser = await upsertSsoUser({
            email,
            name: googleProfile.name ?? null,
            avatarUrl: googleProfile.picture ?? null,
            tenantId: tenant.id,
            authProvider: "google",
            oidcClaims,
          });

          if (!dbUser) {
            console.error("[Google] upsertSsoUser returned null for:", email);
            return false;
          }

          user.id = dbUser.id;
          user.tenant_id = dbUser.tenant_id;
          user.is_admin = dbUser.is_admin;
          user.auth_provider = "google";

          return true;
        } catch (err) {
          console.error("[Google] signIn callback failed:", err);
          return false;
        }
      }

      // ── Azure AD / Entra ID provider ──
      if (account?.provider === "azure-ad") {
        try {
          const email = user.email?.toLowerCase();
          if (!email) {
            console.error("[Azure AD] signIn denied — no email on user object");
            return false;
          }

          let tenant = await getTenantByDomain(email);
          if (!tenant) {
            if (!isPlatformSsoEmailAllowed(email)) {
              console.error("[SSO] Platform tenant fallback denied by invite/domain allowlist:", email);
              return false;
            }
            tenant = await getPlatformTenant();
          }
          if (!tenant) {
            console.error("[Azure AD] No tenant found for email domain:", email);
            return false;
          }

          // Extract group/role claims from the Azure AD id_token
          const oidcClaims: Record<string, unknown> = { email };
          if (account.id_token) {
            try {
              const payload = jose.decodeJwt(account.id_token);
              if (payload.groups) oidcClaims.groups = payload.groups;
              if (payload.roles) oidcClaims.roles = payload.roles;
              if (payload.wids) oidcClaims.wids = payload.wids;
              if (payload.tid) oidcClaims.tid = payload.tid;
            } catch {
              // Token decode failed — continue without group claims
              console.error("[Azure AD] Failed to decode id_token claims for:", email);
            }
          }

          const dbUser = await upsertSsoUser({
            email,
            name: user.name ?? null,
            avatarUrl: user.image ?? null,
            tenantId: tenant.id,
            authProvider: "azure",
            oidcClaims,
          });

          if (!dbUser) {
            console.error("[Azure AD] upsertSsoUser returned null for:", email);
            return false;
          }

          // Propagate DB-resolved fields to the user object for JWT callback
          user.id = dbUser.id;
          user.tenant_id = dbUser.tenant_id;
          user.is_admin = dbUser.is_admin;
          user.auth_provider = "azure";
          if (Array.isArray(oidcClaims.groups)) {
            user.groups = (oidcClaims.groups as unknown[]).map(String);
          }

          return true;
        } catch (err) {
          console.error("[Azure AD] signIn callback failed:", err);
          return false;
        }
      }

      // Other providers (credentials, sso) — pass through
      return true;
    },

    jwt({ token, user }) {
      if (user) {
        token.user_id = user.id as string;
        token.tenant_id = user.tenant_id;
        token.is_admin = user.is_admin;
        token.email = user.email as string;
        if (Array.isArray(user.groups) && user.groups.length > 0) {
          token.groups = user.groups;
        }
      }
      return token;
    },
    session({ session, token }) {
      if (session.user) {
        session.user.id = token.user_id;
        session.user.tenant_id = token.tenant_id;
        session.user.is_admin = token.is_admin;
        session.user.email = token.email;
        if (Array.isArray(token.groups) && token.groups.length > 0) {
          session.user.groups = token.groups;
        }
      }
      return session;
    },
  },
});

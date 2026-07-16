import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";

interface TenantSsoRow {
  id: string;
  name: string;
  oidc_issuer: string | null;
  oidc_client_id: string | null;
  oidc_scopes: string | null;
}

interface OpenIdConfiguration {
  authorization_endpoint?: string;
  code_challenge_methods_supported?: string[];
}

function normalizeEmail(email: string): string {
  return email.trim().toLowerCase();
}

function discoveryUrl(issuer: string): string {
  return `${issuer.replace(/\/$/, "")}/.well-known/openid-configuration`;
}

export async function POST(request: NextRequest) {
  const body = (await request.json().catch(() => null)) as {
    email?: string;
    state?: string;
    code_challenge?: string;
  } | null;

  const email = normalizeEmail(body?.email ?? "");
  const state = body?.state?.trim() ?? "";
  const codeChallenge = body?.code_challenge?.trim() ?? "";
  const domain = email.split("@")[1];

  if (!email || !domain) {
    return NextResponse.json(
      { error: "Enter a valid work email to continue with SSO." },
      { status: 400 },
    );
  }

  if (!state) {
    return NextResponse.json(
      { error: "Missing SSO state." },
      { status: 400 },
    );
  }

  const tenantResult = await pool.query<TenantSsoRow>(
    `SELECT id, name, oidc_issuer, oidc_client_id, oidc_scopes
     FROM tenants
     WHERE sso_enabled = true
       AND allowed_domains @> ARRAY[$1]::text[]
     LIMIT 1`,
    [domain],
  );

  const tenant = tenantResult.rows[0];

  if (!tenant) {
    return NextResponse.json(
      { error: "No tenant-specific SSO is configured for this email domain yet." },
      { status: 404 },
    );
  }

  if (!tenant.oidc_issuer || !tenant.oidc_client_id) {
    return NextResponse.json(
      { error: `SSO is enabled for ${tenant.name}, but its OIDC settings are incomplete.` },
      { status: 400 },
    );
  }

  const configResponse = await fetch(discoveryUrl(tenant.oidc_issuer), {
    cache: "no-store",
  }).catch(() => null);

  if (!configResponse?.ok) {
    return NextResponse.json(
      { error: `Could not load the OIDC discovery document for ${tenant.name}.` },
      { status: 502 },
    );
  }

  const config = (await configResponse.json()) as OpenIdConfiguration;
  if (!config.authorization_endpoint) {
    return NextResponse.json(
      { error: `OIDC discovery for ${tenant.name} is missing an authorization endpoint.` },
      { status: 502 },
    );
  }

  // The redirect URI must be the browser-visible portal origin. In the
  // standalone Docker build request.nextUrl.origin resolves to the server
  // bind address (http://0.0.0.0:3004), which IdPs reject as unregistered —
  // prefer the configured public URL, then forwarded headers, then origin.
  const forwardedHost = request.headers.get("x-forwarded-host");
  const forwardedProto = request.headers.get("x-forwarded-proto") ?? "http";
  const portalBaseUrl =
    process.env.NEXTAUTH_URL?.trim() ||
    process.env.AUTH_URL?.trim() ||
    (forwardedHost ? `${forwardedProto}://${forwardedHost}` : null) ||
    request.nextUrl.origin;
  const redirectUri = new URL("/login/sso/callback", portalBaseUrl).toString();
  const authorizationUrl = new URL(config.authorization_endpoint);

  authorizationUrl.searchParams.set("client_id", tenant.oidc_client_id);
  authorizationUrl.searchParams.set("response_type", "code");
  authorizationUrl.searchParams.set("redirect_uri", redirectUri);
  authorizationUrl.searchParams.set(
    "scope",
    tenant.oidc_scopes?.trim() || "openid email profile",
  );
  authorizationUrl.searchParams.set("state", state);
  authorizationUrl.searchParams.set("login_hint", email);

  const usesPkce =
    Boolean(codeChallenge) &&
    config.code_challenge_methods_supported?.includes("S256");

  if (usesPkce) {
    authorizationUrl.searchParams.set("code_challenge", codeChallenge);
    authorizationUrl.searchParams.set("code_challenge_method", "S256");
  }

  return NextResponse.json({
    authorizationUrl: authorizationUrl.toString(),
    redirectUri,
    tenantName: tenant.name,
    usesPkce,
  });
}

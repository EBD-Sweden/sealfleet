import { NextResponse } from "next/server";
import { getPublicJwk } from "@/lib/auth-keys";

/**
 * Public JWKS endpoint.
 *
 * Backend services (e.g. the MCP router) verify portal-issued session JWTs
 * against this document. The `kid` in each JWT header matches the `kid` of
 * the JWK published here, allowing key rotation without service restarts.
 *
 * URL: `${NEXTAUTH_URL}/api/.well-known/jwks.json`
 */
export async function GET() {
  const jwk = await getPublicJwk();
  return NextResponse.json(
    { keys: [jwk] },
    {
      headers: {
        "Cache-Control": "public, max-age=300",
        "Content-Type": "application/jwk-set+json",
      },
    }
  );
}

// JWKS material is derived at request time from a cached keypair; nothing here
// is request-specific, but we keep it dynamic so new keys can be picked up
// without redeploying when the env var changes.
export const dynamic = "force-dynamic";

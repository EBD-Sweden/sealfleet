// Public self-serve signup: creates a tenant + admin user + first API key.
// No session required (this is how you get your first account). Must be listed
// as a public path in portal-route-policy.ts.

import { NextRequest, NextResponse } from "next/server";
import { createTenantWithAdmin, SignupError } from "@/lib/provisioning";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  let body: { org?: string; email?: string; password?: string; name?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  if (process.env.DISABLE_SELF_SIGNUP === "true") {
    return NextResponse.json({ error: "Self-serve signup is disabled" }, { status: 403 });
  }

  try {
    const result = await createTenantWithAdmin({
      org: body.org ?? "",
      email: body.email ?? "",
      password: body.password ?? "",
      name: body.name,
    });
    // The API key is returned ONCE here so the operator can copy it; it is not
    // retrievable again. Everything else the user reaches by logging in.
    return NextResponse.json(
      {
        ok: true,
        tenant_slug: result.tenantSlug,
        api_key: result.apiKey,
        message: "Account created. Sign in to continue. Save your API key — it is shown only once.",
      },
      { status: 201 },
    );
  } catch (err) {
    if (err instanceof SignupError) {
      return NextResponse.json({ error: err.message }, { status: err.status });
    }
    console.error("signup failed:", err);
    return NextResponse.json({ error: "Signup failed" }, { status: 500 });
  }
}

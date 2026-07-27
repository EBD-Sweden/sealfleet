// Completes AWS Marketplace registration: reads the signed cookie from the
// fulfillment step, creates a Sealfleet tenant, links it to the AWS customer, and
// activates it if AWS reports an active entitlement. Public (authenticated by the
// signed cookie, not a session).

import { NextRequest, NextResponse } from "next/server";
import { jwtVerify } from "jose";
import { createTenantWithAdmin, SignupError } from "@/lib/provisioning";
import { getAwsCustomer, linkCustomerToTenant, hasActiveEntitlement } from "@/lib/aws-marketplace";
import { setTenantKeysActive } from "@/lib/billing";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const cookie = req.cookies.get("aws_mp")?.value;
  if (!cookie) return NextResponse.json({ error: "No marketplace session; start from AWS Marketplace" }, { status: 400 });

  let cid: string;
  try {
    const secret = new TextEncoder().encode(process.env.NEXTAUTH_SECRET || "dev-secret");
    const { payload } = await jwtVerify(cookie, secret);
    cid = String(payload.cid);
  } catch {
    return NextResponse.json({ error: "Marketplace session expired; start again from AWS Marketplace" }, { status: 400 });
  }

  const existing = await getAwsCustomer(cid);
  if (!existing) return NextResponse.json({ error: "Unknown marketplace customer" }, { status: 400 });
  if (existing.tenant_id) return NextResponse.json({ error: "This subscription is already linked to an account" }, { status: 409 });

  const body = await req.json().catch(() => ({}));
  try {
    const result = await createTenantWithAdmin({
      org: body.org ?? "",
      email: body.email ?? "",
      password: body.password ?? "",
    });
    const entitled = await hasActiveEntitlement(cid).catch(() => false);
    await linkCustomerToTenant(cid, result.tenantId, entitled ? "active" : "pending");
    // Keys are active from provisioning; if AWS hasn't confirmed the entitlement
    // yet, hold access until the subscribe-success notification arrives.
    if (!entitled) await setTenantKeysActive(result.tenantId, false);

    const res = NextResponse.json({
      ok: true,
      tenant_slug: result.tenantSlug,
      api_key: result.apiKey,
      entitled,
      message: entitled
        ? "Account created and activated. Save your API key (shown once)."
        : "Account created. Access activates once AWS confirms your subscription (usually within a minute).",
    }, { status: 201 });
    res.cookies.delete("aws_mp");
    return res;
  } catch (err) {
    if (err instanceof SignupError) return NextResponse.json({ error: err.message }, { status: err.status });
    console.error("aws-marketplace register failed:", err);
    return NextResponse.json({ error: "Registration failed" }, { status: 500 });
  }
}

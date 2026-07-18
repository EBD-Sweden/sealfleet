// Start a Stripe Checkout for the logged-in tenant's Enterprise subscription.
// Returns { url } to redirect the browser to. Session-gated.

import { NextRequest, NextResponse } from "next/server";
import { requirePortalSession } from "@/lib/portal-auth";
import { createCheckoutSession, stripeConfigured, availablePlans, planByKey } from "@/lib/stripe";
import { getSubscription } from "@/lib/billing";

export const runtime = "nodejs";

function baseUrl(req: NextRequest): string {
  return (
    process.env.PORTAL_PUBLIC_URL ||
    process.env.NEXTAUTH_URL ||
    process.env.AUTH_URL ||
    req.nextUrl.origin
  );
}

export async function POST(req: NextRequest) {
  const { error, context } = await requirePortalSession();
  if (error) return error;
  const tenantId = context!.user.tenant_id;
  if (!tenantId) return NextResponse.json({ error: "No tenant on session" }, { status: 400 });

  const plans = availablePlans();
  if (!stripeConfigured() || plans.length === 0) {
    return NextResponse.json({ error: "Billing is not configured" }, { status: 503 });
  }

  // Pick the requested plan (default: first available).
  const body = await req.json().catch(() => ({}));
  const plan = (body?.plan ? planByKey(body.plan) : undefined) ?? plans[0];

  const sub = await getSubscription(tenantId);
  const origin = baseUrl(req);

  try {
    const { url } = await createCheckoutSession({
      tenantId,
      email: context!.user.email,
      customerId: sub?.stripe_customer_id ?? undefined,
      priceId: plan.priceId,
      metered: plan.metered,
      successUrl: `${origin}/billing?checkout=success`,
      cancelUrl: `${origin}/billing?checkout=cancelled`,
    });
    return NextResponse.json({ url });
  } catch (err) {
    console.error("checkout failed:", err);
    return NextResponse.json({ error: "Could not start checkout" }, { status: 502 });
  }
}

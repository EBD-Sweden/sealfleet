// Open the Stripe Billing Portal so a subscribed tenant can manage or cancel
// their plan and update payment details. Session-gated. Returns { url }.

import { NextRequest, NextResponse } from "next/server";
import { requirePortalSession } from "@/lib/portal-auth";
import { createBillingPortalSession, stripeConfigured } from "@/lib/stripe";
import { getSubscription } from "@/lib/billing";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const { error, context } = await requirePortalSession();
  if (error) return error;
  const tenantId = context!.user.tenant_id;
  if (!tenantId) return NextResponse.json({ error: "No tenant on session" }, { status: 400 });
  if (!stripeConfigured()) {
    return NextResponse.json({ error: "Billing is not configured" }, { status: 503 });
  }

  const sub = await getSubscription(tenantId);
  if (!sub?.stripe_customer_id) {
    return NextResponse.json({ error: "No active billing account" }, { status: 404 });
  }

  const origin =
    process.env.PORTAL_PUBLIC_URL || process.env.NEXTAUTH_URL || process.env.AUTH_URL || req.nextUrl.origin;
  try {
    const { url } = await createBillingPortalSession(sub.stripe_customer_id, `${origin}/billing`);
    return NextResponse.json({ url });
  } catch (err) {
    console.error("billing portal failed:", err);
    return NextResponse.json({ error: "Could not open billing portal" }, { status: 502 });
  }
}

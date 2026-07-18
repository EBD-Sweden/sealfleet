// Billing status for the logged-in tenant: subscription state + recent usage +
// whether billing is even configured on this deployment. Drives the /billing UI.

import { NextResponse } from "next/server";
import { requirePortalSession } from "@/lib/portal-auth";
import { getSubscription, usageCount, isEntitled } from "@/lib/billing";
import { stripeConfigured, stripePriceId } from "@/lib/stripe";

export const runtime = "nodejs";

export async function GET() {
  const { error, context } = await requirePortalSession();
  if (error) return error;
  const tenantId = context!.user.tenant_id;
  if (!tenantId) return NextResponse.json({ error: "No tenant on session" }, { status: 400 });

  const sub = await getSubscription(tenantId);
  const monthStart = new Date();
  monthStart.setUTCDate(1);
  monthStart.setUTCHours(0, 0, 0, 0);
  const usage = await usageCount(tenantId, monthStart);

  return NextResponse.json({
    billing_enabled: stripeConfigured() && Boolean(stripePriceId()),
    status: sub?.status ?? "inactive",
    entitled: isEntitled(sub?.status),
    plan: sub?.plan ?? null,
    current_period_end: sub?.current_period_end ?? null,
    has_customer: Boolean(sub?.stripe_customer_id),
    usage_this_month: usage,
  });
}

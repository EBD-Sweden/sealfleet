// Report metered API usage to Stripe. Called on a schedule (e.g. Cloud
// Scheduler hourly), NOT by a user — so it authenticates with a shared secret
// header (BILLING_CRON_SECRET), not a session. Public path in the route policy.
//
// For each tenant on the metered (usage) plan it counts api_key_usage_log rows
// since the last watermark and reports the delta as one Stripe meter event,
// then advances the watermark so each call is billed exactly once.

import { NextRequest, NextResponse } from "next/server";
import { reportMeterEvent } from "@/lib/stripe";
import { meteredSubscriptions, usageCountBetween, advanceUsageWatermark } from "@/lib/billing";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const secret = process.env.BILLING_CRON_SECRET;
  const usagePriceId = process.env.STRIPE_PRICE_HOSTED_USAGE;
  if (!secret || !process.env.STRIPE_SECRET_KEY || !usagePriceId) {
    return NextResponse.json({ error: "Usage reporting not configured" }, { status: 503 });
  }
  if (req.headers.get("x-billing-cron-secret") !== secret) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const to = new Date();
  const subs = await meteredSubscriptions(usagePriceId);
  const reported: Array<{ tenant: string; calls: number }> = [];

  for (const s of subs) {
    // First time we see this subscription, just plant the watermark — don't
    // bill usage that predates the subscription. Metering starts from here.
    if (!s.usage_reported_through) {
      await advanceUsageWatermark(s.tenant_id, to);
      continue;
    }
    const from = new Date(s.usage_reported_through);
    const calls = await usageCountBetween(s.tenant_id, from, to);
    try {
      if (calls > 0) {
        await reportMeterEvent(s.stripe_customer_id, calls, `${s.tenant_id}-${to.getTime()}`);
        reported.push({ tenant: s.tenant_id, calls });
      }
      // Advance the watermark even at 0 calls so the next window starts here.
      await advanceUsageWatermark(s.tenant_id, to);
    } catch (err) {
      // Leave the watermark unmoved on failure so the next run retries this span.
      console.error(`usage report failed for tenant ${s.tenant_id}:`, err);
    }
  }

  return NextResponse.json({ ok: true, tenants: subs.length, reported });
}

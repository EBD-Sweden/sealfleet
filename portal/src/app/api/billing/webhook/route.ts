// Stripe webhook. Public path (Stripe calls it, no session) but authenticated by
// signature verification against STRIPE_WEBHOOK_SECRET. Keeps the subscriptions
// table in sync and flips the tenant's API keys active/inactive on
// entitlement changes (the actual access enforcement).

import { NextRequest, NextResponse } from "next/server";
import { verifyWebhook, type StripeObject } from "@/lib/stripe";
import {
  upsertSubscription,
  setTenantKeysActive,
  findTenantIdByCustomer,
  isEntitled,
  type SubStatus,
} from "@/lib/billing";

export const runtime = "nodejs";

function periodEnd(sub: StripeObject): Date | null {
  const secs = sub.current_period_end;
  return typeof secs === "number" ? new Date(secs * 1000) : null;
}

async function tenantIdFor(obj: StripeObject): Promise<string | null> {
  return (
    obj.metadata?.tenant_id ||
    obj.client_reference_id ||
    (obj.customer ? await findTenantIdByCustomer(obj.customer) : null) ||
    null
  );
}

export async function POST(req: NextRequest) {
  const secret = process.env.STRIPE_WEBHOOK_SECRET;
  if (!secret) return NextResponse.json({ error: "Webhook not configured" }, { status: 503 });

  const raw = await req.text();
  let event;
  try {
    event = verifyWebhook(raw, req.headers.get("stripe-signature"), secret, Math.floor(Date.now() / 1000));
  } catch (err) {
    console.warn("stripe webhook signature rejected:", (err as Error).message);
    return NextResponse.json({ error: "Invalid signature" }, { status: 400 });
  }

  try {
    const obj: StripeObject = event.data?.object ?? {};
    switch (event.type) {
      case "checkout.session.completed": {
        const tenantId = await tenantIdFor(obj);
        if (tenantId) {
          await upsertSubscription({
            tenantId,
            customerId: obj.customer ?? null,
            subscriptionId: obj.subscription ?? null,
            status: "active",
          });
          await setTenantKeysActive(tenantId, true);
        }
        break;
      }
      case "customer.subscription.created":
      case "customer.subscription.updated":
      case "customer.subscription.deleted": {
        const tenantId = await tenantIdFor(obj);
        if (tenantId) {
          const status = (
            event.type === "customer.subscription.deleted" ? "canceled" : obj.status ?? "inactive"
          ) as SubStatus;
          const item = obj.items?.data?.[0];
          await upsertSubscription({
            tenantId,
            customerId: obj.customer ?? null,
            subscriptionId: obj.id ?? null,
            status,
            plan: item?.price?.id ?? null,
            seats: item?.quantity ?? null,
            currentPeriodEnd: periodEnd(obj),
          });
          await setTenantKeysActive(tenantId, isEntitled(status));
        }
        break;
      }
      default:
        // Ignore unrelated events.
        break;
    }
  } catch (err) {
    console.error("stripe webhook handling failed:", err);
    return NextResponse.json({ error: "Handler error" }, { status: 500 });
  }

  return NextResponse.json({ received: true });
}

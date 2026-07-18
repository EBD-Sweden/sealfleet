// Minimal Stripe client — talks to the Stripe REST API directly with fetch and
// verifies webhooks with Node crypto, so we add NO npm dependency (keeps the
// portal image small and the supply chain tight). Covers exactly what the
// hosted billing flow needs: Checkout, Billing Portal, and webhook verification.

import crypto from "crypto";

const STRIPE_API = "https://api.stripe.com/v1";

// Minimal shapes of the Stripe objects/events this integration touches. Stripe
// payloads carry far more; we type only what the webhook/handlers read.
export interface StripeObject {
  id?: string;
  customer?: string | null;
  subscription?: string | null;
  status?: string;
  client_reference_id?: string | null;
  metadata?: Record<string, string> | null;
  current_period_end?: number;
  items?: { data?: Array<{ price?: { id?: string }; quantity?: number }> };
}

export interface StripeEvent {
  type: string;
  data?: { object?: StripeObject };
}

export function stripeConfigured(): boolean {
  return Boolean(process.env.STRIPE_SECRET_KEY);
}

export function stripePriceId(): string {
  return process.env.STRIPE_PRICE_ENTERPRISE ?? "";
}

function secretKey(): string {
  const k = process.env.STRIPE_SECRET_KEY;
  if (!k) throw new Error("STRIPE_SECRET_KEY is not set");
  return k;
}

export type PlanKey = "monthly" | "annual" | "usage";

export interface Plan {
  key: PlanKey;
  label: string;
  priceId: string;
  metered: boolean;
  blurb: string;
}

// The self-serve plans, resolved from env price IDs. A plan appears only if its
// price ID is configured, so a deployment can offer any subset.
export function availablePlans(): Plan[] {
  const defs: Array<Omit<Plan, "priceId"> & { env: string }> = [
    { key: "monthly", label: "Monthly", metered: false, env: "STRIPE_PRICE_HOSTED_MONTHLY",
      blurb: "Flat monthly, includes a generous call allowance." },
    { key: "annual", label: "Annual", metered: false, env: "STRIPE_PRICE_HOSTED_ANNUAL",
      blurb: "Best value — save vs monthly, billed yearly." },
    { key: "usage", label: "Usage-only", metered: true, env: "STRIPE_PRICE_HOSTED_USAGE",
      blurb: "No base fee — pay only for API calls. Easiest way to start." },
  ];
  const plans: Plan[] = [];
  for (const d of defs) {
    const priceId = process.env[d.env];
    if (priceId) plans.push({ key: d.key, label: d.label, priceId, metered: d.metered, blurb: d.blurb });
  }
  // Back-compat: if only the legacy single price is set, expose it as monthly.
  if (plans.length === 0 && stripePriceId()) {
    plans.push({ key: "monthly", label: "Subscribe", priceId: stripePriceId(), metered: false,
      blurb: "Enterprise subscription." });
  }
  return plans;
}

export function planByKey(key: string): Plan | undefined {
  return availablePlans().find((p) => p.key === key);
}

// Report usage to a Stripe Billing Meter. `value` is the number of API calls in
// the window; `identifier` dedupes retries. Used by the report-usage job.
export async function reportMeterEvent(
  customerId: string,
  value: number,
  identifier: string,
): Promise<void> {
  const eventName = process.env.STRIPE_METER_EVENT_NAME || "sealfleet_api_calls";
  await stripePost("/billing/meter_events", {
    event_name: eventName,
    identifier,
    "payload[stripe_customer_id]": customerId,
    "payload[value]": String(value),
  });
}

// Stripe expects application/x-www-form-urlencoded with bracket notation for
// nested params, e.g. line_items[0][price]=price_123.
function encodeForm(obj: Record<string, unknown>, prefix = ""): string[] {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(obj)) {
    if (value === undefined || value === null) continue;
    const name = prefix ? `${prefix}[${key}]` : key;
    if (typeof value === "object") {
      parts.push(...encodeForm(value as Record<string, unknown>, name));
    } else {
      parts.push(`${encodeURIComponent(name)}=${encodeURIComponent(String(value))}`);
    }
  }
  return parts;
}

async function stripePost<T = Record<string, unknown>>(
  path: string,
  body: Record<string, unknown>,
): Promise<T> {
  const res = await fetch(`${STRIPE_API}${path}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${secretKey()}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: encodeForm(body).join("&"),
  });
  const json = (await res.json()) as { error?: { message?: string } };
  if (!res.ok) {
    throw new Error(`Stripe ${path} failed: ${json?.error?.message ?? res.status}`);
  }
  return json as T;
}

export interface CheckoutParams {
  tenantId: string;
  email?: string;
  customerId?: string;
  successUrl: string;
  cancelUrl: string;
  quantity?: number;
  priceId?: string;
  // Metered prices cannot carry a quantity in Checkout.
  metered?: boolean;
}

// Create a subscription Checkout Session and return its hosted URL.
export async function createCheckoutSession(p: CheckoutParams): Promise<{ id: string; url: string }> {
  const price = p.priceId || stripePriceId();
  if (!price) throw new Error("No Stripe price configured (STRIPE_PRICE_ENTERPRISE or a plan price)");
  const lineItem: Record<string, unknown> = p.metered ? { price } : { price, quantity: p.quantity ?? 1 };
  const body: Record<string, unknown> = {
    mode: "subscription",
    "line_items": [lineItem],
    success_url: p.successUrl,
    cancel_url: p.cancelUrl,
    client_reference_id: p.tenantId,
    // Stamp tenant_id on BOTH the session and the resulting subscription so the
    // webhook can map any subscription event back to a tenant.
    metadata: { tenant_id: p.tenantId },
    subscription_data: { metadata: { tenant_id: p.tenantId } },
    allow_promotion_codes: true,
  };
  if (p.customerId) body.customer = p.customerId;
  else if (p.email) body.customer_email = p.email;
  const session = await stripePost<{ id: string; url: string }>("/checkout/sessions", body);
  return { id: session.id, url: session.url };
}

// Create a Billing Portal session so a customer can manage/cancel their plan.
export async function createBillingPortalSession(customerId: string, returnUrl: string): Promise<{ url: string }> {
  const session = await stripePost<{ url: string }>("/billing_portal/sessions", {
    customer: customerId,
    return_url: returnUrl,
  });
  return { url: session.url };
}

// Verify a Stripe webhook signature (the `Stripe-Signature` header) against the
// raw request body. Mirrors Stripe's scheme: v1 = HMAC-SHA256 of `${t}.${body}`.
// tolerance defaults to 5 minutes to reject replayed events.
export function verifyWebhook(
  rawBody: string,
  sigHeader: string | null,
  secret: string,
  nowSeconds: number,
  toleranceSeconds = 300,
): StripeEvent {
  if (!sigHeader) throw new Error("Missing Stripe-Signature header");
  const parts = Object.fromEntries(
    sigHeader.split(",").map((kv) => {
      const [k, v] = kv.split("=");
      return [k, v];
    }),
  );
  const t = parts["t"];
  const v1 = parts["v1"];
  if (!t || !v1) throw new Error("Malformed Stripe-Signature header");

  const timestamp = Number(t);
  if (!Number.isFinite(timestamp) || Math.abs(nowSeconds - timestamp) > toleranceSeconds) {
    throw new Error("Stripe-Signature timestamp outside tolerance");
  }

  const expected = crypto
    .createHmac("sha256", secret)
    .update(`${t}.${rawBody}`, "utf8")
    .digest("hex");

  const a = Buffer.from(expected);
  const b = Buffer.from(v1);
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) {
    throw new Error("Stripe-Signature verification failed");
  }
  return JSON.parse(rawBody);
}

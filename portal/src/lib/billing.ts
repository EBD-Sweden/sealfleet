// Subscription state for the hosted (direct-billing) service. A tenant is
// "entitled" to use the hosted platform while its Stripe subscription is
// trialing or active; on cancellation/non-payment we deactivate the tenant's
// API keys (the router already refuses inactive keys), which is the actual
// access enforcement — no per-request billing lookup needed.

import { pool } from "@/lib/db";

export type SubStatus = "inactive" | "trialing" | "active" | "past_due" | "canceled";

export interface Subscription {
  tenant_id: string;
  stripe_customer_id: string | null;
  stripe_subscription_id: string | null;
  status: SubStatus;
  plan: string | null;
  seats: number | null;
  current_period_end: string | null;
}

const ENTITLED: SubStatus[] = ["trialing", "active"];

export function isEntitled(status: string | null | undefined): boolean {
  return ENTITLED.includes((status ?? "inactive") as SubStatus);
}

export async function getSubscription(tenantId: string): Promise<Subscription | null> {
  const { rows } = await pool.query<Subscription>(
    `SELECT tenant_id, stripe_customer_id, stripe_subscription_id, status, plan, seats, current_period_end
     FROM subscriptions WHERE tenant_id = $1`,
    [tenantId],
  );
  return rows[0] ?? null;
}

export async function findTenantIdByCustomer(customerId: string): Promise<string | null> {
  const { rows } = await pool.query<{ tenant_id: string }>(
    "SELECT tenant_id FROM subscriptions WHERE stripe_customer_id = $1 LIMIT 1",
    [customerId],
  );
  return rows[0]?.tenant_id ?? null;
}

export interface UpsertSub {
  tenantId: string;
  customerId?: string | null;
  subscriptionId?: string | null;
  status: SubStatus;
  plan?: string | null;
  seats?: number | null;
  currentPeriodEnd?: Date | null;
}

export async function upsertSubscription(s: UpsertSub): Promise<void> {
  await pool.query(
    `INSERT INTO subscriptions
        (tenant_id, stripe_customer_id, stripe_subscription_id, status, plan, seats, current_period_end, updated_at)
     VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
     ON CONFLICT (tenant_id) DO UPDATE SET
        stripe_customer_id     = COALESCE(EXCLUDED.stripe_customer_id, subscriptions.stripe_customer_id),
        stripe_subscription_id = COALESCE(EXCLUDED.stripe_subscription_id, subscriptions.stripe_subscription_id),
        status                 = EXCLUDED.status,
        plan                   = COALESCE(EXCLUDED.plan, subscriptions.plan),
        seats                  = COALESCE(EXCLUDED.seats, subscriptions.seats),
        current_period_end     = COALESCE(EXCLUDED.current_period_end, subscriptions.current_period_end),
        updated_at             = NOW()`,
    [
      s.tenantId,
      s.customerId ?? null,
      s.subscriptionId ?? null,
      s.status,
      s.plan ?? null,
      s.seats ?? null,
      s.currentPeriodEnd ?? null,
    ],
  );
}

// Enable/disable all of a tenant's API keys. api_keys.tenant_id is TEXT holding
// the tenant UUID as a string (see provisioning.ts), so we match on that.
export async function setTenantKeysActive(tenantId: string, active: boolean): Promise<void> {
  await pool.query("UPDATE api_keys SET is_active = $2 WHERE tenant_id = $1", [tenantId, active]);
}

// Usage total for a tenant over a window — the feed for metered billing / a
// usage widget. Counts logged API calls since `since`.
export async function usageCount(tenantId: string, since: Date): Promise<number> {
  const { rows } = await pool.query<{ n: string }>(
    "SELECT COUNT(*)::text AS n FROM api_key_usage_log WHERE tenant_id = $1 AND created_at >= $2",
    [tenantId, since],
  );
  return Number(rows[0]?.n ?? 0);
}

// Calls in the half-open window (from, to] — the increment the usage reporter
// bills for. `from` null means "from the beginning".
export async function usageCountBetween(tenantId: string, from: Date | null, to: Date): Promise<number> {
  const { rows } = await pool.query<{ n: string }>(
    `SELECT COUNT(*)::text AS n FROM api_key_usage_log
     WHERE tenant_id = $1 AND created_at <= $3 AND ($2::timestamptz IS NULL OR created_at > $2)`,
    [tenantId, from, to],
  );
  return Number(rows[0]?.n ?? 0);
}

export interface MeteredSub {
  tenant_id: string;
  stripe_customer_id: string;
  usage_reported_through: string | null;
}

// Active/trialing subscriptions on the metered (usage) plan that have a Stripe
// customer — the ones the reporter should push meter events for.
export async function meteredSubscriptions(usagePriceId: string): Promise<MeteredSub[]> {
  const { rows } = await pool.query<MeteredSub>(
    `SELECT tenant_id, stripe_customer_id, usage_reported_through
     FROM subscriptions
     WHERE status IN ('active','trialing')
       AND stripe_customer_id IS NOT NULL
       AND plan = $1`,
    [usagePriceId],
  );
  return rows;
}

export async function advanceUsageWatermark(tenantId: string, through: Date): Promise<void> {
  await pool.query("UPDATE subscriptions SET usage_reported_through = $2 WHERE tenant_id = $1", [
    tenantId,
    through,
  ]);
}

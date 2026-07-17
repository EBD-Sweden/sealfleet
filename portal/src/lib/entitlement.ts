// Sealfleet open-core entitlement (portal side).
//
// The router is the single source of truth for the license (it verifies the
// signed key / AWS Marketplace entitlement). The portal fetches GET /license,
// caches it briefly, and gates enterprise-only flows (SSO, multi-user).
//
// Fail-closed: if the router is unreachable or errors, enterprise features are
// treated as LOCKED (free tier), so an outage can never silently unlock SSO.

const ROUTER_URL = process.env.ROUTER_URL || "http://mcp-router:8040";
const CACHE_TTL_MS = 60_000;

export interface Entitlement {
  tier: "free" | "enterprise";
  features: string[];
  seats: number;
  customer: string;
}

const FREE: Entitlement = { tier: "free", features: [], seats: 1, customer: "" };

let cache: { ent: Entitlement; at: number } | null = null;

export async function getEntitlement(): Promise<Entitlement> {
  const now = Date.now();
  if (cache && now - cache.at < CACHE_TTL_MS) return cache.ent;
  try {
    const res = await fetch(`${ROUTER_URL}/license`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    if (!res.ok) throw new Error(`license ${res.status}`);
    const j = (await res.json()) as Partial<Entitlement>;
    const ent: Entitlement = {
      tier: j.tier === "enterprise" ? "enterprise" : "free",
      features: Array.isArray(j.features) ? j.features : [],
      seats: typeof j.seats === "number" ? j.seats : 1,
      customer: j.customer ?? "",
    };
    cache = { ent, at: now };
    return ent;
  } catch {
    // Fail closed to free — never unlock enterprise features on an error.
    cache = { ent: FREE, at: now };
    return FREE;
  }
}

export async function hasFeature(feature: string): Promise<boolean> {
  return (await getEntitlement()).features.includes(feature);
}

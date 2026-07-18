# Billing (hosted service — Stripe)

The hosted service is a **direct-billing** product: customers self-serve sign up,
then subscribe with Stripe. (This is separate from the self-hosted **license-key**
path — see `LICENSING.md`. A self-hosted customer buys a license key; a hosted
customer pays you via Stripe.)

## How it works

```
signup ──▶ POST /api/signup ──▶ tenant + admin user + API key   (db/migrations/014)
                                    │  key is_active = true (trial)
login  ──▶ /billing ──▶ POST /api/billing/checkout ──▶ Stripe Checkout
                                    │
Stripe ──▶ POST /api/billing/webhook (signature-verified)
                                    │  upsert subscriptions row
                                    ▼
             subscription active/trialing ──▶ keep tenant API keys active
             canceled / past_due          ──▶ deactivate tenant API keys
```

Enforcement is deliberately simple and robust: **a tenant's access is its API
keys' `is_active` flag**, which the router already checks on every request. The
webhook flips that flag on subscription changes — no per-request billing lookup,
no new middleware. On cancellation the keys go inactive and the router refuses
them.

Feature entitlement (SSO/SCIM/etc.) still comes from the platform license
(`runtime/licensing.py`); the hosted deployment runs as the Enterprise tier
(bundled key + optional `SEALFLEET_LICENSE_KEY`). Stripe controls **who may use
the service**, the license controls **which features exist**.

## Components

| Piece | File |
|---|---|
| Signup (public) | `portal/src/app/api/signup/route.ts`, `portal/src/lib/provisioning.ts` |
| Signup UI | `portal/src/app/signup/page.tsx` |
| Checkout | `portal/src/app/api/billing/checkout/route.ts` |
| Webhook (public, signed) | `portal/src/app/api/billing/webhook/route.ts` |
| Manage / cancel | `portal/src/app/api/billing/portal/route.ts` |
| Status + usage | `portal/src/app/api/billing/status/route.ts` |
| Billing UI | `portal/src/app/billing/page.tsx` |
| Stripe client (no SDK) | `portal/src/lib/stripe.ts` |
| Subscription state | `portal/src/lib/billing.ts` |
| Schema | `db/migrations/014_billing.sql` |

No `stripe` npm package is used — the client talks to the Stripe REST API with
`fetch` and verifies webhooks with Node `crypto` (HMAC-SHA256, ±5-min tolerance),
so the portal image gains no dependency.

## Stripe setup (EBD Sweden AB account)

1. **Product + price.** Stripe Dashboard → *Product catalog* → create
   **"Sealfleet Enterprise"** with a **recurring** price (e.g. monthly). Copy the
   **Price ID** (`price_…`). For usage-based billing, add a **metered** price and
   report usage from `api_key_usage_log` (see *Metered billing*, below).
2. **API key.** *Developers → API keys* → copy the **secret key** (`sk_live_…`).
3. **Webhook.** *Developers → Webhooks* → add endpoint
   `https://app.sealfleet.example.com/api/billing/webhook`, subscribe to:
   `checkout.session.completed`, `customer.subscription.created`,
   `customer.subscription.updated`, `customer.subscription.deleted`. Copy the
   **signing secret** (`whsec_…`).

## Configuration (env)

| Env | Purpose |
|---|---|
| `STRIPE_SECRET_KEY` | `sk_live_…` — server API calls. Empty ⇒ billing disabled (portal shows "not configured"). |
| `STRIPE_WEBHOOK_SECRET` | `whsec_…` — webhook signature verification. |
| `STRIPE_PRICE_ENTERPRISE` | `price_…` — the plan the Subscribe button checks out. |
| `PORTAL_PUBLIC_URL` | Public portal URL for Checkout success/cancel + return links. |
| `DISABLE_SELF_SIGNUP` | `true` to turn off `/api/signup` (e.g. self-hosted single-tenant). |

For the hosted Cloud Run module, set these as Terraform vars —
`stripe_secret_key`, `stripe_webhook_secret`, `stripe_price_enterprise`; the
secrets go to Secret Manager automatically
(`deploy/terraform/hosted-cloudrun/`).

For a **self-hosted / Helm** deployment, pass them through `portal.extraEnv` in
your values (self-hosted usually wants `DISABLE_SELF_SIGNUP=true` and the
license-key path instead — billing is primarily for the hosted service):

```yaml
portal:
  extraEnv:
    STRIPE_SECRET_KEY: sk_live_…
    STRIPE_WEBHOOK_SECRET: whsec_…
    STRIPE_PRICE_ENTERPRISE: price_…
```

## Testing locally

Use Stripe **test mode** keys and the Stripe CLI to forward webhooks:

```bash
stripe listen --forward-to localhost:3004/api/billing/webhook
# copy the printed whsec_… into STRIPE_WEBHOOK_SECRET, then:
stripe trigger checkout.session.completed
```

## Metered billing (next step)

The schema + metering feed are in place: the router writes every API call to
`api_key_usage_log`, and `usageCount(tenantId, since)` (`portal/src/lib/billing.ts`)
aggregates it. To bill per-usage, add a scheduled job that sums each tenant's
calls per period and reports them to Stripe as **usage records** against a
metered price. Not wired yet — flat subscription is the default.

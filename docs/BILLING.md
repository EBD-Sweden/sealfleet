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

## Plans

The `/billing` page offers whichever of these plans have a price ID configured,
so a new customer can "get started easily":

| Plan | env | Kind | Lookup key |
|---|---|---|---|
| Hosted Monthly | `STRIPE_PRICE_HOSTED_MONTHLY` | flat recurring | `sealfleet_hosted_monthly` |
| Hosted Annual | `STRIPE_PRICE_HOSTED_ANNUAL` | flat recurring | `sealfleet_hosted_annual` |
| Hosted Usage-only | `STRIPE_PRICE_HOSTED_USAGE` | metered (meter `sealfleet_api_calls`) | `sealfleet_hosted_usage` |

`POST /api/billing/checkout {"plan":"monthly|annual|usage"}` starts Checkout for
the chosen plan. The self-hosted **license** products (annual/monthly) are sold
separately (license key / AWS Marketplace), not through this self-serve flow.

## Stripe setup (Evid Invest / EBD Sweden account)

The product catalog is created by `scripts/stripe-setup` (idempotent, keyed by
price `lookup_key`), or in the Dashboard. It creates two products —
**"Sealfleet Enterprise — Hosted"** (monthly/annual/usage/overage prices) and
**"Sealfleet Enterprise — Self-Hosted License"** (annual/monthly) — plus the
**`sealfleet_api_calls`** Billing Meter.

Then:
1. **API key.** *Developers → API keys* → **secret key** (`sk_live_…`).
2. **Webhook.** *Developers → Webhooks* → add endpoint
   `https://app.sealfleet.example.com/api/billing/webhook`, subscribe to:
   `checkout.session.completed`, `customer.subscription.created`,
   `customer.subscription.updated`, `customer.subscription.deleted`. Copy the
   **signing secret** (`whsec_…`).

## Configuration (env)

| Env | Purpose |
|---|---|
| `STRIPE_SECRET_KEY` | `sk_live_…` — server API calls. Empty ⇒ billing disabled (portal shows "not configured"). |
| `STRIPE_WEBHOOK_SECRET` | `whsec_…` — webhook signature verification. |
| `STRIPE_PRICE_HOSTED_MONTHLY` / `_ANNUAL` / `_USAGE` | `price_…` — the plans offered on `/billing`. Set any subset. |
| `STRIPE_PRICE_ENTERPRISE` | Legacy single price — used as "monthly" if the per-plan IDs are unset. |
| `STRIPE_METER_EVENT_NAME` | Billing Meter `event_name` the usage reporter emits (default `sealfleet_api_calls`). |
| `BILLING_CRON_SECRET` | Shared secret the usage-report cron sends as `x-billing-cron-secret`. |
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

## Metered (usage) billing

Wired end-to-end. The router writes every API call to `api_key_usage_log`; the
**usage reporter** (`POST /api/billing/report-usage`) aggregates each metered
tenant's calls since a per-tenant watermark and pushes one Stripe **meter event**
(`sealfleet_api_calls`) per window, then advances the watermark so each call is
billed exactly once. A brand-new subscription's watermark is planted on first
tick so pre-subscription usage isn't billed.

The endpoint authenticates with the `x-billing-cron-secret` header (not a
session), so schedule it however you like:

- **Hosted Cloud Run:** the Terraform module creates a **Cloud Scheduler** job
  (`usage_report_schedule`, default hourly) that POSTs it — enable the
  `cloudscheduler.googleapis.com` API. Created only when metered billing is
  configured (`stripe_price_hosted_usage` + `stripe_secret_key` set).
- **Self-hosted / k8s:** a CronJob (or any cron) hitting the same URL with the
  header.

Tune the rate on the meter's price (`sealfleet_hosted_usage`, €49/1M by default).

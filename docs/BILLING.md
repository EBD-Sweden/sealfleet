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

## AWS Marketplace (self-host license)

The AWS Marketplace channel sells a **self-host license**: the customer runs
Sealfleet in **their own** infra (BYOF Terraform) and unlocks Enterprise features
with a signed license token. We run **nothing** for them on AWS — so it costs us
~$0 until a deal closes (and even then it's in the buyer's account). This is
distinct from the hosted GCP Cloud Run SaaS (which is the "try and use" cloud
version). The license token is signed **offline** with the operator-held private
key, which never touches a server.

For SaaS products created since 2026-06-01, AWS Marketplace Concurrent
Agreements no longer returns the legacy `CustomerIdentifier` from
`ResolveCustomer`; the portal treats `LicenseArn` as the durable subscription
identity and filters `GetEntitlements` by `LICENSE_ARN` for those agreements.
Migration `018_aws_marketplace_concurrent_agreements.sql` records the LicenseArn
and CustomerAWSAccountId alongside the existing operator queue row.

Operator flow:

1. After subscribing, AWS POSTs the buyer to **`/api/aws-marketplace`** with
   `x-amzn-marketplace-token`. We call **ResolveCustomer**, stash a signed cookie,
   and redirect to **`/aws-marketplace`**.
2. The buyer submits **contact details** (org + email — no account, no password).
   **`/api/aws-marketplace/register`** records them, checks **GetEntitlements**,
   and **notifies the operator** that a license must be issued. No hosted tenant is
   created.
3. **`/api/aws-marketplace/notification`** (SNS, signature-verified) tracks the
   subscription lifecycle and alerts the operator on `subscribe-success` (issue the
   license) and `unsubscribe-success` (revoke / do not renew). `unsubscribe-pending`
   just sets a grace status.

**Operator runbook (per subscription):**

```
# 1. An alert arrives (log line, or MARKETPLACE_ALERT_WEBHOOK) once the entitlement
#    is confirmed. Find the queue:
psql "$DATABASE_URL" -c \
  "SELECT customer_identifier, org_name, contact_email, status
     FROM aws_marketplace_customers WHERE license_issued = false AND status = 'active';"

# 2. Issue the token OFFLINE with the private key (see LICENSING.md):
python scripts/sealfleet-license.py issue --private <path/to/private.key> \
  --customer "<org_name> — AWS Marketplace" --days 365

# 3. Email the token + the self-host setup guide to contact_email, then close it out:
psql "$DATABASE_URL" -c \
  "UPDATE aws_marketplace_customers
      SET license_issued = true, license_issued_at = NOW()
    WHERE customer_identifier = '<customer_identifier>';"
```

Schema: `db/migrations/016_aws_marketplace.sql` + `017_aws_marketplace_selfhost.sql`
(`aws_marketplace_customers` — the operator's work queue). Code:
`portal/src/lib/aws-marketplace.ts` + the routes above. Uses the AWS SDK
(`@aws-sdk/client-marketplace-metering`, `-entitlement-service`).

**Config (portal env):** `SEALFLEET_AWS_MARKETPLACE_PRODUCT_CODE`, AWS creds
(either `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` for an IAM user scoped to
`aws-marketplace:ResolveCustomer` + `GetEntitlements`, **or** an App Runner /
instance role with `AWS_ROLE_ARN` set), `AWS_MARKETPLACE_REGION` (us-east-1), and
optional `MARKETPLACE_ALERT_WEBHOOK` (Slack/generic webhook for issue/revoke
alerts). Point the AWS Marketplace product's **fulfillment URL** at
`https://<portal>/api/aws-marketplace` and its **SNS topic** at
`https://<portal>/api/aws-marketplace/notification`. Because fulfillment is served
by the already-live GCP portal, **no always-on AWS resource is required**.

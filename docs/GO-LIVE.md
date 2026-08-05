# Go-live guide (operator / seller setup)

Step-by-step for the manual, account-level setup that has to happen outside the
repo before the hosted service and the paid license/marketplace channels are
live. Do these once. Everything here is **your** action (needs your logins);
the code + Terraform is already in the repo.

Order that makes sense:
1. [GCP project](#1-gcp-project-for-the-hosted-service) — where the hosted service runs
2. [Neon Postgres](#2-neon-serverless-postgres) — the scale-to-zero database
3. [First `terraform apply`](#3-first-terraform-apply) — brings the hosted service up
4. [Stripe product](#4-stripe-product-billing) — billing (see also `docs/BILLING.md`)
5. [AWS Marketplace](#5-aws-marketplace-seller-registration) — the entitlement channel

---

## 1. GCP project (for the hosted service)

The hosted service runs on Cloud Run in a GCP project you own. Terraform module:
`deploy/terraform/hosted-cloudrun/`.

```bash
# a) Create (or pick) a project. Use your EBD Sweden billing account.
gcloud projects create sealfleet-hosted --name="Sealfleet Hosted"
gcloud billing projects link sealfleet-hosted \
  --billing-account=XXXXXX-XXXXXX-XXXXXX      # gcloud billing accounts list

# b) Point local gcloud at it and authenticate for Terraform (ADC).
gcloud config set project sealfleet-hosted
gcloud auth application-default login          # <-- run this in YOUR terminal (use ! prefix in-session)

# c) Enable the APIs the module needs.
gcloud services enable run.googleapis.com secretmanager.googleapis.com \
  compute.googleapis.com --project sealfleet-hosted
```

Notes:
- **Region:** the module defaults to `europe-north1` (Finland — closest low-carbon
  GCP region to Sweden). Override with `-var region=...`.
- **Public invocation:** the module sets `allow_public_invoke=true` (Sealfleet's own
  auth protects the services). Some GCP orgs have a policy
  (`constraints/iam.allowedPolicyMemberDomains`) that blocks `allUsers`. If apply
  fails on the IAM binding, either ask your org admin to allow it for this project
  or set `-var allow_public_invoke=false` and put the services behind an
  IAP/load-balancer.
- **Cost while idle:** ~$0. Cloud Run `min_instances=0` bills only per request;
  Secret Manager is pennies. The only standing cost is the domain, if you map one.

## 2. Neon serverless Postgres

Neon is a Postgres that scales to zero — no idle DB cost. (Supabase works too;
Neon is the simplest.)

1. Sign up at <https://neon.tech> with the EBD Sweden account. Free tier is fine to start.
2. **Create a project** → pick region **AWS eu-north-1 (Stockholm)** or **eu-central-1**
   to sit near Cloud Run `europe-north1`.
3. In the project → **Connection Details**, copy the **pooled** connection string
   (host contains `-pooler`), and append the DB + sslmode:
   ```
   postgresql://USER:PASSWORD@ep-xxxx-pooler.eu-central-1.aws.neon.tech/sealfleet?sslmode=require
   ```
   Use the **pooler** endpoint — Cloud Run opens many short-lived connections and
   the pooler keeps you under Neon's connection cap.
4. Keep this string secret. You'll pass it to Terraform as `database_url` (it's
   stored in GCP Secret Manager, not in state as plaintext output).

**Autosuspend caveat:** Neon free-tier suspends the compute after ~5 min idle, so
the *first* query after idle adds ~0.5s on top of the Cloud Run cold start. Fine
for early customers; bump Neon's autosuspend or set Cloud Run `min_instances=1`
once you have steady traffic.

## 3. First `terraform apply`

```bash
cd deploy/terraform/hosted-cloudrun
terraform init
terraform apply \
  -var 'project_id=sealfleet-hosted' \
  -var 'region=europe-north1' \
  -var 'database_url=postgresql://…-pooler.…neon.tech/sealfleet?sslmode=require'
  # portal_public_url left empty on first apply; set it after you see the URL
```

Then apply the schema **once** to Neon (the module provisions services, not schema):

```bash
export DATABASE_URL='postgresql://…-pooler.…neon.tech/sealfleet?sslmode=require'
for f in db/migrations/*.sql; do psql "$DATABASE_URL" -f "$f"; done
```

`terraform output` prints the portal/router URLs. Set `portal_public_url` to the
portal URL (or a custom domain) and re-apply so `NEXTAUTH_URL` is correct. To map
a domain:

```bash
gcloud run domain-mappings create --service sealfleet-portal \
  --domain app.sealfleet.example.com --region europe-north1
# then add the CNAME/A records it prints to the example.com DNS zone
```

## 4. Stripe product (billing)

You said Stripe is already connected under **EBD Sweden AB**. The product + prices
and the exact env wiring live in **`docs/BILLING.md`** — follow that. Summary of
the account-level part:

1. Stripe Dashboard → **Product catalog** → create **"Sealfleet Enterprise"** with a
   recurring monthly price (and optionally a metered usage price for API calls).
2. Copy the **price ID(s)** (`price_...`) and the **secret key** (`sk_live_...`) and
   set up a **webhook** to `https://app.sealfleet.example.com/api/billing/webhook`
   (copy its signing secret `whsec_...`).
3. Put `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ENTERPRISE` into
   the portal's Secret Manager env (add them as `-var` secrets or Secret Manager
   entries the portal reads).

## 5. AWS Marketplace seller registration

This is the second paid channel: AWS Marketplace sells the self-host license, and
buyers run Sealfleet in their own infra with BYOF Terraform. The fulfillment
handler now lives at `/api/aws-marketplace`: it resolves the buyer, records their
contact details, confirms entitlement via GetEntitlements/SNS, and alerts the
operator to issue the signed license key offline. Registration is **manual,
one-time**, and takes a few days for AWS to approve tax/banking.

**Steps:**
1. Go to the **AWS Marketplace Management Portal**:
   <https://aws.amazon.com/marketplace/management/> — sign in with the EBD Sweden
   AWS account (the `568069990865` org account, or a dedicated seller account).
2. **Register as a seller**: complete the seller profile (legal name *EBD Sweden AB*,
   address), then the **tax (W-8BEN-E** for a Swedish entity**)** and **banking**
   information. AWS reviews this — allow 3–5 business days. You can't publish a paid
   product until it clears.
3. **Choose product type: "SaaS"** (not AMI/container) — Sealfleet is delivered as
   a self-host license, and the SaaS model with **contract pricing** matches the
   license/entitlement workflow. This gives you Marketplace Metering
   `ResolveCustomer` plus the Entitlement API that the portal fulfillment routes
   use (`SEALFLEET_AWS_MARKETPLACE_PRODUCT_CODE`).
4. **Create the product** (SaaS Subscription or SaaS Contract). You'll get a
   **Product Code** — put it in the Helm value `licensing.awsMarketplaceProductCode`
   / env `SEALFLEET_AWS_MARKETPLACE_PRODUCT_CODE` so entitlement checks resolve.
5. **Fulfillment URL:** point the Marketplace product to
   `https://<portal>/api/aws-marketplace`. AWS POSTs `x-amzn-marketplace-token`;
   the route calls `ResolveCustomer`, stores a signed short-lived cookie, and
   redirects the buyer to `/aws-marketplace` to leave contact details. Configure
   the product's SNS subscription lifecycle topic to
   `https://<portal>/api/aws-marketplace/notification`.
6. Submit for **listing review**. AWS reviews the listing before it goes public.

**Programmatic upkeep (after the manual launch):** once the product exists, use the
**AWS Marketplace Catalog API** (`aws marketplace-catalog start-change-set`) to update
pricing/versions from CI — no console clicking for routine changes. Initial creation
must be done in the console.

> You said you'll do the seller registration — steps 1–2 are all you. Ping me for
> steps 3–6 (product config + final fulfillment URL/SNS wiring) when the account clears.

---

### Quick status

| Item | State | Owner |
|---|---|---|
| GCP project + ADC | to do | you (guide above) |
| Neon project + conn string | to do | you (guide above) |
| `terraform apply` hosted module | ready to run | you (or me, once creds exist) |
| Stripe product + keys | Stripe connected; product to create | you (`docs/BILLING.md`) |
| Stripe webhook wiring (code) | see `docs/BILLING.md` | built in repo |
| AWS Marketplace seller reg | to do (3–5 day approval) | you (steps 1–2) |
| AWS product + fulfillment handler | built; wiring needs product code + URLs | me/operator, when account clears |

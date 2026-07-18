# Hosted Sealfleet on Cloud Run (scale-to-zero)

Run Sealfleet as a **managed service** with **~$0 idle cost**: the platform runs
as Cloud Run services with `min_instances=0` (pay only for actual request time),
new customers are **tenants in a shared database** (no per-customer infra), and
the database is a **serverless Postgres** (Neon / Supabase) that also scales to
zero. This is the cheapest way to offer a hosted product before you have steady
traffic. See [`docs/HOSTED.md`](../../../docs/HOSTED.md) for the full picture.

Requires images `>= v0.3.0` (Cloud Run-native `$PORT` support).

## Prerequisites

- A GCP project + `gcloud` authenticated; enable `run`, `secretmanager`,
  `compute` APIs.
- A **serverless Postgres** with a connection string, e.g. a free
  [Neon](https://neon.tech) project (`postgresql://user:pass@ep-xxx.neon.tech/sealfleet?sslmode=require`).

## Deploy

```bash
cd deploy/terraform/hosted-cloudrun
terraform init
terraform apply \
  -var 'project_id=my-project' \
  -var 'region=europe-north1' \
  -var 'database_url=postgresql://…neon.tech/sealfleet?sslmode=require' \
  -var 'portal_public_url=https://app.sealfleet.example.com'
```

Then, **once**, apply the schema to your Postgres (this module provisions
services, not schema):

```bash
# from a machine with psql + this repo checked out
export DATABASE_URL='postgresql://…neon.tech/sealfleet?sslmode=require'
for f in db/migrations/*.sql; do psql "$DATABASE_URL" -f "$f"; done
psql "$DATABASE_URL" -f scripts/001_create_api_keys.sql   # optional: seed an operator key
```

`terraform output` gives the portal/router URLs. If you left `portal_public_url`
empty, set it to the portal URL (or your custom domain) and re-apply so
`NEXTAUTH_URL` is correct, then map the domain:

```bash
gcloud run domain-mappings create --service sealfleet-portal \
  --domain app.sealfleet.example.com --region europe-north1
```

## Cost model

- **Idle:** ~$0 — Cloud Run scales to zero; Neon free tier scales to zero.
- **Active:** Cloud Run request-time (~$0.000024/vCPU-s; 2M requests/mo free) +
  Postgres. A handful of light tenants ≈ a few $/month.
- **Cold start:** first request after idle ~1–2s. Set `min_instances=1` once you
  have steady traffic to remove it.

## Notes

- Services allow public invocation by default (`allow_public_invoke`);
  Sealfleet's own auth (`REQUIRE_AUTH`, portal login) protects them. Set it
  `false` to require IAM-authenticated invocation (e.g. behind an IAP/LB).
- The git-to-k8s **deploy service** is intentionally omitted (there's no k8s in
  this topology). Hosted MCPs can be added as their own Cloud Run services.
- Platform secrets (encryption key, RS256 key, NextAuth secret) are generated
  and stored in Secret Manager; pass your own via variables to override.

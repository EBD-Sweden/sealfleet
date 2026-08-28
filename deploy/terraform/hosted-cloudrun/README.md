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
psql "$DATABASE_URL" -f scripts/001_create_api_keys.sql
```

Migrations are idempotent; re-run the loop after every upgrade. 0.8.0 adds
`018_aws_marketplace_concurrent_agreements`, `019_credentials` (the
credential store — previously missing entirely) and `020_pipelines_v2`
(runtime-authored pipelines, which on Cloud Run must live in the database).

### Register the runtime API key (required for the chat)

The portal, the chat agent and the llm-proxy share one operator key
(`runtime_api_key`; generated when you don't set it). The router only accepts
it once it is in `api_keys`, and the chat needs `allow_identity_delegation`
so its calls are attributed to the real user:

```bash
KEY=$(terraform output -raw runtime_api_key)
psql "$DATABASE_URL" -c "INSERT INTO api_keys
  (api_key, tenant_id, name, is_active, action_permissions, allow_identity_delegation)
  VALUES ('$KEY', 'hosted', 'Hosted portal/agent key', true,
          ARRAY['pipeline.invoke','agent.invoke','agent.register','mcp.server.register','registry.export','registry.import'],
          true)
  ON CONFLICT (api_key) DO UPDATE SET is_active = true,
    action_permissions = EXCLUDED.action_permissions,
    allow_identity_delegation = EXCLUDED.allow_identity_delegation;"
```

This key deliberately excludes `credential.*` — the chat resolves a tenant's
own LLM credentials as the logged-in user, never as the service.

### Images

Build and push all five services (the chat adds `agent` and
`runtime/llm-proxy`):

```bash
PUSH=1 TAG=0.8.0 scripts/build-hosted-images.sh runtime registry portal agent runtime/llm-proxy
```

### Chat brains on hosted

There is no on-box model on Cloud Run. Set `anthropic_api_key` for a
platform-level default brain (`chat_model`, default
`anthropic/claude-sonnet-4-6`), and/or let tenants bring their own: a
credential scoped `llm:anthropic`, `llm:openai`, … appears in their chat's
brain picker and is unwrapped as that user. The platform's control plane is
also an MCP server at `<router_url>/mcp` (see COMPONENTS.md).

`terraform output` gives the portal/router/agent/llm-proxy URLs. If you left `portal_public_url`
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

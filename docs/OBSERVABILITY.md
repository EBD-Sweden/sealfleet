# Observability, Trace, and Audit Receipts

Sealfleet emits trace IDs and immutable audit rows for security-sensitive runtime paths. The LLM receives only opaque handles, trace IDs, and redacted receipts; credentials and sealed plaintext bypass the model.

## Receipt schema

Structured audit events are written to `audit_events` with:

- `tenant_id`
- `user_id`
- `action`
- `resource`
- `server_name`
- `result`
- `trace_id`
- `duration_ms`
- redacted JSON `payload`
- `audit_hash_version` (`canonical-payload-v1` for current rows; explicit legacy markers are required before `/audit/verify` will excuse unrecoverable pre-canonical JSONB key-order rows)
- append-only `created_at`

Audit reads stay authenticated. Runtime audit access is gated by `audit.read`; no anonymous audit/log read endpoint was added. `/audit/verify` fails closed on canonical or unmarked payload mismatches; only rows explicitly marked as `legacy-json-payload-order` (or older untagged rows that predate purpose/lawful-basis tagging) are reported as `legacy_unverifiable_payload_seqs` while chain linkage remains enforced.

## Current emission map

| Path | Event action | Trace behavior | Redaction boundary |
| --- | --- | --- | --- |
| Runtime `/pipeline` MCP call | `tool_call` | One trace ID per pipeline, propagated to policy/sealed/tool receipts | Tool errors only; sealed plaintext is never persisted |
| Runtime named pipeline | `tool_call` | One trace ID per run | Tool errors only; sealed plaintext is never persisted |
| Policy deny / confirm-required | `policy_deny`, `policy_confirm_required` | Uses caller/pipeline trace ID | Rule ID and reason only |
| Sealed handle create/delete/HTTP resolve | `sealed_handle.create`, `sealed_handle.delete`, `sealed_handle.resolve` | Endpoint-generated trace IDs where applicable | Labels and reasons only; no sealed values |
| In-process sealed resolve | `sealed_handle.resolve` | Receives pipeline trace ID | Handle label only; no plaintext |
| RFC 8693 token exchange | `token_exchange` | One trace ID per exchange | No subject/access tokens in audit payload |
| Deploy catalog registration | `deploy.register` | One trace ID per deploy pipeline | Env var values for secret-like keys are redacted; DB-registration SSE failures return a generic message, leave redacted detail in the audit receipt when available, and remain generic even if the failure-audit sink is unavailable |
| Runtime audit read | `/audit/events` response | Requires `audit.read` | Tenant-scoped unless global audit permission is explicit |
| Portal test console | Runtime call response includes `trace_id`; portal does not expose audit reads anonymously | Runtime-sourced trace | Portal health/ready responses contain no env/secrets |

## Health and readiness

Public health/readiness endpoints are intentionally bounded and do not expose secrets:

- Runtime: `GET /health`, `GET /ready`
- Registry: `GET /health`, `GET /ready`
- Deploy: `GET /health`, `GET /ready`
- Portal: `GET /api/health`, `GET /api/ready`

Kubernetes probes should use readiness endpoints for startup/routing decisions and health endpoints for liveness.

## OpenTelemetry backend config

The lightweight runtime audit table is independent of OTEL export. For distributed traces/log export, configure standard OTEL environment variables on services:

```bash
OTEL_SERVICE_NAME=mcpfinder-runtime
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_EXPORTER_OTLP_PROTOCOL=grpc
OTEL_RESOURCE_ATTRIBUTES=deployment.environment=public-test,service.namespace=mcpfinder
```

Recommended collector pipeline:

1. Receive OTLP from runtime, registry, deploy, portal, and MCP workloads.
2. Drop or hash request bodies and authorization headers at the collector.
3. Export traces/metrics to the operator backend.
4. Keep structured security audit events in Postgres with tenant-scoped reads.

## Operator smoke

Run against the local clone-run ports (`8010`, `8030`, `8040`) or the k3d host-port mappings with a scoped runtime API key:

```bash
RUNTIME_API_KEY=<key-with-audit.read> \
RUNTIME_URL=http://localhost:8040 \
REGISTRY_URL=http://localhost:8010 \
DEPLOY_URL=http://localhost:8030 \
python scripts/smoke-observability-receipts.py
```

The script checks readiness, attempts a bounded demo pipeline call when a runtime key is present, reads recent audit events through the authenticated endpoint, and prints actionable diagnostics on failure.

### Self-hosted temporary audit key lifecycle

In a self-hosted clone-and-run or local k3d environment, create a short-lived smoke key directly in the operator-owned database, run the smoke, then revoke it. Generate the key in the shell; do not paste it into docs, commits, or chat:

```bash
export RUNTIME_API_KEY="smoke_$(openssl rand -hex 24)"
export SMOKE_TENANT_ID="demo-sandbox"

psql "$DATABASE_URL" \
  -v runtime_api_key="$RUNTIME_API_KEY" \
  -v smoke_tenant_id="$SMOKE_TENANT_ID" <<'SQL'
INSERT INTO api_keys (api_key, tenant_id, name, is_active, action_permissions, metadata)
VALUES (:'runtime_api_key', :'smoke_tenant_id', 'temporary observability smoke', true, ARRAY['pipeline.invoke','audit.read'], '{"purpose":"temporary-observability-smoke"}'::jsonb)
ON CONFLICT (api_key) DO UPDATE
SET is_active = true,
    action_permissions = EXCLUDED.action_permissions,
    metadata = EXCLUDED.metadata;
SQL

RUNTIME_URL=http://localhost:8040 \
REGISTRY_URL=http://localhost:8010 \
DEPLOY_URL=http://localhost:8030 \
python scripts/smoke-observability-receipts.py

psql "$DATABASE_URL" -v runtime_api_key="$RUNTIME_API_KEY" <<'SQL'
UPDATE api_keys SET is_active = false WHERE api_key = :'runtime_api_key';
SQL
unset RUNTIME_API_KEY SMOKE_TENANT_ID
```

If the runtime already cached active keys, wait for the runtime API-key refresh interval or restart/reconcile the runtime deployment before the authenticated part of the smoke. For k3d, the documented host ports are `8040` → runtime, `8010` → registry, `8030` → deploy, and `3004` → portal; the internal Kubernetes NodePort numbers are not necessarily bound to identical host ports by k3d.

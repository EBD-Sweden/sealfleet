# OSS self-hosted demo quickstart

Public-test for Sealfleet means an open-source clone-and-run path. A tester should be able to clone the repo, run deterministic fake demos, and understand the difference between local OSS mode and operator-managed Kubernetes smoke without receiving any private credentials.

## Safety contract

- Fake data only: no customer records, production credentials, broker keys, or real downstream systems.
- No embedded secrets: examples use environment variables or Kubernetes `Secret` references only.
- Deterministic demo: the default `scripts/demo-openapi-to-mcp.py --invoke` path performs no network calls and no deployment.
- Local and Kubernetes modes are separate: local clone-run is the public-test happy path; k3d/Kubernetes is QA/operator evidence.
- Cleanup is explicit and demo-scoped.

## Mode A: deterministic local demo, no database required

### Prerequisites

- Linux/macOS shell
- Python 3.11+
- Git

### Commands

```bash
git clone https://github.com/<your-org>/mcpfinder.git
cd mcpfinder
python3 -m venv runtime/.venv
runtime/.venv/bin/python -m pip install -r runtime/requirements.txt
runtime/.venv/bin/python scripts/demo-openapi-to-mcp.py --invoke
```

Expected output:

The output should match this shape:

```json
{
  "artifact_dir": ".../runtime/.generated/demo-fake-crm-mcp",
  "mode": "public_demo",
  "tenant_id": "demo-sandbox",
  "workspace_id": "demo-external-evaluation",
  "spec_ref": "checked-in:fake-crm-openapi",
  "deploy_action": "dry_run",
  "invocation": {
    "tool": "get_demo_customer",
    "arguments": {"customer_id": "CUST-DEMO-001"},
    "classification": "fake-demo-only"
  }
}
```

The command writes fake-data-only artifacts under ignored local scratch space at `runtime/.generated/demo-fake-crm-mcp/`. It does not contact external services, does not read credentials, and does not deploy containers. The checked-in fixture under `runtime/generated/demo-fake-crm-mcp/` is source-controlled example material and is not touched by the default command.

Cleanup:

```bash
rm -rf runtime/.generated/demo-fake-crm-mcp
# Optional: reset the checked-in fixture if you intentionally used --output-dir runtime/generated
# git restore runtime/generated/demo-fake-crm-mcp
```

## Mode B: local services with seeded fake demo tenant

Use this when you want the router/registry/deploy/portal services running locally. It requires a local PostgreSQL instance but still uses fake demo data.

### Prerequisites

- Python 3.11+
- Node.js/npm for the portal
- PostgreSQL reachable on `localhost:54323`
- Database `mcpfinder`
- User `admin`
- Password supplied by `PGPASSWORD` in your shell; do not commit it or paste it into docs

### Setup commands

```bash
python3 -m venv runtime/.venv
runtime/.venv/bin/python -m pip install -r runtime/requirements.txt -r registry/requirements.txt -r deploy/requirements.txt
cd portal && env -u npm_config_prefix npm install && cd ..

export PGPASSWORD="${PGPASSWORD:?set your local Postgres password}"
for f in db/migrations/*.sql; do
  psql -h localhost -p 54323 -U admin -d mcpfinder -f "$f"
done
psql -h localhost -p 54323 -U admin -d mcpfinder -f db/seeds/010_demo_sandbox.sql
```

Seeded fake demo objects:

- Tenant: `demo-sandbox`
- Org: `Sealfleet Demo Org`
- Workspace identity: `demo-external-evaluation`
- Demo identity placeholder: `demo.viewer@mcpfinder.dev` (inactive; activate through the auth layer, not a checked-in password)
- Sample MCP: `demo-sandbox-mcp`
- Sample pipeline: `demo_sandbox_invoice_review`
- Data classification: `fake-demo-only`

There is no first-class `workspaces` table in the current schema; the concrete workspace binding is enforced by runtime auth metadata (`tenant_id=demo-sandbox`, `workspace_id`/`X-Workspace-ID=demo-external-evaluation`) and by the v2 pipeline safety block.

### Start, inspect, and stop

```bash
./scripts/start-local.sh
./scripts/start-local.sh --status
```

Expected result: registry `:8010`, deploy `:8030`, router `:8040`, and portal `:3000` report healthy/running. Logs are written under `runtime/logs/` and PIDs under `runtime/.local-pids`.

Stop local services:

```bash
./scripts/start-local.sh --stop
```

## Running the sample sandbox pipeline

Use the portal test console or runtime v2 pipeline API with a sandbox-scoped session/API token. The authenticated runtime tenant must be `demo-sandbox`; pass the workspace with auth metadata/header (`X-Workspace-ID: demo-external-evaluation`). Example request shape:

```bash
export DEMO_AUTH_VALUE="${DEMO_AUTH_VALUE:?set sandbox-scoped bearer value}"
curl -fsS http://localhost:8040/v2/pipelines/run \
  -H "Authorization: Bearer ${DEMO_AUTH_VALUE}" \
  -H 'X-Workspace-ID: demo-external-evaluation' \
  -H 'Content-Type: application/json' \
  --data @- <<'JSON'
{
  "pipeline": "demo_sandbox_invoice_review",
  "inputs": {
    "workspace": "demo-external-evaluation",
    "invoice_id": "INV-DEMO-001",
    "amount_usd": 12450,
    "vendor_name": "Northwind Demo Supplies",
    "country": "SE",
    "risk_hint": "fake data: new vendor, normal payment terms"
  }
}
JSON
```

Expected output:

The output should match this shape:

```json
{
  "classification": "fake-demo-only",
  "invoice_summary": {
    "invoice_id": "INV-DEMO-001",
    "status": "review_required",
    "reasons": [
      "amount exceeds demo auto-approve threshold",
      "fake vendor onboarding check required"
    ]
  },
  "vendor_score": {
    "vendor_name": "Northwind Demo Supplies",
    "score": 72,
    "tier": "demo-medium"
  }
}
```

The runtime rejects non-`demo-sandbox` tenant context, non-`demo-external-evaluation` workspace context, over-64 KiB request bodies, and more than 10 demo pipeline runs/hour.

## Quotas and rate limits

Default demo limits:

- Enforced by the runtime v2 pipeline guard: 10 demo pipeline runs/hour per tenant/workspace/pipeline bucket.
- Enforced by the runtime v2 pipeline guard: 64 KiB max request body.
- Operator/deployment responsibility until an edge limiter is wired: optional 20 demo MCP requests/minute per user/IP.
- Operator/deployment responsibility: remove demo run artifacts/logs older than 24 hours.
- One active sandbox workspace per invited tester, represented by runtime auth metadata (`workspace_id` / `X-Workspace-ID`) because there is no first-class workspaces table yet.

Operator health smoke, when the portal is configured on the legacy external-demo port:

```bash
curl -fsS http://localhost:3004/api/health | python -m json.tool
```

## Mode C: Kubernetes/k3d operator smoke

Kubernetes is not the primary public-test user path. Use it as release evidence after local clone-run docs pass.

```bash
NAMESPACE=demo-sandbox DRY_RUN=1 scripts/k8s-demo-smoke.sh
NAMESPACE=demo-sandbox DRY_RUN=0 scripts/k8s-demo-smoke.sh --cleanup
```

The script fails if unexpected pods are `ImagePullBackOff`, `CrashLoopBackOff`, `Evicted`, `Error`, or `ContainerStatusUnknown`, and it can delete stale failed pods/jobs before re-running smoke. Cleanup is guarded: it refuses to delete unless the namespace is `demo-sandbox` or an operator explicitly sets `ALLOW_NON_DEMO_NAMESPACE=1`, and the label selector contains `mcpfinder-demo-sandbox`.

Required Kubernetes secrets are referenced directly in the `k8s/*.yaml` manifests. Do not commit Secret manifests with `data` or `stringData` values.

For local k3d clusters, public-test manifests use `imagePullPolicy: IfNotPresent` because `:latest` otherwise forces a registry pull on every scale-from-zero transition. If Docker's embedded DNS cannot resolve the k3d registry alias from node containers (`lookup <registry>: Try again`), keep the public manifest unchanged and pre-load the image into each schedulable k3d node's containerd cache instead of committing host-specific registry IPs:

```bash
scripts/k3d-cache-image.sh <your-registry>/<your-mcp-image>:latest
kubectl apply -f k8s/demo-sandbox-mcp.yaml
kubectl rollout status deploy/demo-sandbox-mcp -n demo-sandbox --timeout=180s
```

The helper keeps the exact manifest image tag portable, exports it with `docker image save`, discovers each schedulable k3d node with `kubectl get nodes`, imports the archive into each node via `ctr -n k8s.io images import`, and verifies the result with `crictl images`/`crictl image inspect`. This prevents Kubernetes from scheduling the first scale-from-zero replica onto a node that still has to pull from the flaky registry alias.

This keeps the demo image reference portable while avoiding `ImagePullBackOff` during operator smoke. If Docker cannot pull `localhost:5050/...`, build or load the image into the host Docker cache first and then re-run `scripts/k3d-cache-image.sh`; do not encode transient container IPs into checked-in manifests. If local disk pressure makes kubelet garbage-collect unused cached images, either free disk below kubelet's image GC threshold before smoke or apply a temporary, uncommitted node selector to the warmed node for that public-test run.

## Sandbox safety boundaries

- Tenant/workspace scoped: demo users are restricted to `demo-sandbox` and `demo-external-evaluation`.
- No plaintext secret handling in the default local demo.
- No anonymous side effects: all pipeline/API calls beyond public health/discovery require an authenticated sandbox-scoped principal.
- Network bounded: `k8s/demo-sandbox-mcp.yaml` includes a NetworkPolicy that denies demo MCP egress, and the local demo MCP performs no outbound network/file/secret access.
- Audit required: pipeline runs should return trace/audit receipts, and production-like deployments should keep the sanitized audit sink enabled.

## Troubleshooting

- `runtime/.venv/bin/python: No such file`: run `python3 -m venv runtime/.venv` first.
- Python import errors: rerun `runtime/.venv/bin/python -m pip install -r runtime/requirements.txt`.
- `psql: connection refused`: start local PostgreSQL and confirm it listens on `localhost:54323`.
- `PGPASSWORD: parameter null or not set`: export `PGPASSWORD` in your shell; do not write it into this repo.
- `./scripts/start-local.sh` says the Python venv is missing: create `runtime/.venv` and install runtime requirements.
- Portal dependency errors: run `cd portal && env -u npm_config_prefix npm install`.
- Port already in use: run `./scripts/start-local.sh --stop`, or stop the process currently using ports `8010`, `8030`, `8040`, `8041`, or `3000`.
- Kubernetes cleanup refuses to run: verify `NAMESPACE=demo-sandbox` and that the selector contains `mcpfinder-demo-sandbox` before opting into non-demo cleanup.

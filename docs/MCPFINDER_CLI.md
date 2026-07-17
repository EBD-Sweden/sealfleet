# Sealfleet CLI Quickstart

Sealfleet CLI means the Sealfleet Command Line Interface: a deterministic command-line contract for agents and operators that need to validate Sealfleet-scoped config, check the runtime, invoke MCP tools, and use runtime control-plane APIs.

This CLI is project-specific. It must not use Aether, OpenSnow, or other board/product names in config or command contracts.

## Contract

```bash
runtime/.venv/bin/python -m runtime.cli --json contract
```

The canonical entrypoint is `python -m runtime.cli`; `scripts/mcpfinder_cli.py` and `scripts/mcpfinder-cli` are compatibility wrappers only.

The contract declares:

- config schema: `mcpfinder.cli.config/v1`
- command surface: `contract`, `validate`, `status`, `invoke`, `registry`, `manifest`, `smoke`, `cluster`, `mcp`, `pipeline`, `workflow`
- runtime API mapping: `/health`, `/ready`, `/call`, `/registry/export`, `/registry/import`, `/manifests`, `/manifests/{name}`, `/manifests/typed`
- agent contract: secrets stay out of prompt/payload logs, raw API keys are never echoed, and unavailable backends fail honestly.

## Config validation

Example config:

```json
{
  "schema": "mcpfinder.cli.config/v1",
  "product": "mcpfinder",
  "name": "local-mcpfinder-cli",
  "runtime_url": "http://localhost:8040",
  "allowed_scopes": ["runtime", "registry", "control-plane"]
}
```

Validate:

```bash
runtime/.venv/bin/python -m runtime.cli --json validate --config ./mcpfinder-cli.json
```

Validation rejects cross-project bleed such as `product: example-other-product` or other cross-product names.

## Deterministic no-backend smoke

These commands require no running services and no secrets:

```bash
runtime/.venv/bin/python -m runtime.cli --json contract
runtime/.venv/bin/python -m runtime.cli --json invoke \
  --mcp demo-sandbox-mcp \
  --tool get_demo_customer \
  --payload '{"customer_id":"cust_123"}' \
  --dry-run
runtime/.venv/bin/python -m runtime.cli --json smoke local-demo --dry-run
runtime/.venv/bin/python -m pytest runtime/tests/test_mcpfinder_cli.py -q
```

Expected result: the invoke dry-run prints the exact `/call` request body without making a network call, and `smoke local-demo --dry-run` prints the real `/health`, `/ready`, `/manifests`, and `/call` operations it would run.

## Runtime smoke

With `./scripts/start-local.sh` running the router on `:8040`:

```bash
runtime/.venv/bin/python -m runtime.cli --json status --runtime-url http://localhost:8040
runtime/.venv/bin/python -m runtime.cli --json smoke local-demo \
  --runtime-url http://localhost:8040 \
  --api-key "$MCPFINDER_API_KEY"
```

Expected result: `health` and `ready` responses are returned. If the router is down, CLI returns `backend_unavailable` and a non-zero exit instead of printing fake success.

## Invoke a tool through the runtime

```bash
export MCPFINDER_API_KEY="<local key with mcp.tool.call permission>"
runtime/.venv/bin/python -m runtime.cli --json invoke \
  --runtime-url http://localhost:8040 \
  --mcp demo-sandbox-mcp \
  --tool get_demo_customer \
  --payload '{"customer_id":"CUST-DEMO-001"}'
```

`--payload` and `--payload-file` must resolve to a JSON object. Missing API keys fail with `auth_missing`; runtime HTTP denials are returned as structured `backend_error` details with secret-looking fields redacted.

## Control-plane commands

Export registry metadata for the authenticated tenant:

```bash
runtime/.venv/bin/python -m runtime.cli --json registry export \
  --runtime-url http://localhost:8040 \
  --api-key "$MCPFINDER_API_KEY" \
  --output /tmp/mcpfinder-registry-export.json
```

Validate an import bundle without mutation:

```bash
runtime/.venv/bin/python -m runtime.cli --json registry import \
  --runtime-url http://localhost:8040 \
  --api-key "$MCPFINDER_API_KEY" \
  --input /tmp/mcpfinder-registry-export.json \
  --dry-run
```

Register a manifest, or dry-run to show the target endpoint and redacted manifest:

```bash
runtime/.venv/bin/python -m runtime.cli --json manifest register \
  --runtime-url http://localhost:8040 \
  --api-key "$MCPFINDER_API_KEY" \
  --file runtime/manifests/demo-sandbox-mcp.yaml \
  --typed \
  --dry-run
```

Control-plane calls require a real runtime backend and explicit API key. If the backend is not running, CLI exits non-zero with `backend_unavailable`; if the command cannot be safely performed locally, it exits with a structured error instead of returning a success-looking stub.

## Cluster lifecycle

The `cluster` group provisions and inspects a local dev cluster. It shells out to real tooling (`scripts/start-local.sh` for `--mode local`; `k3d` + `kubectl` for `--mode k3d`) and fails honestly with a structured error and non-zero exit when that tooling or a backend is absent. Every command supports `--dry-run` and the global `--json`.

URL resolution order is flag > env (`MCPFINDER_RUNTIME_URL`, `MCPFINDER_DEPLOY_URL`, `MCPFINDER_KUBE_CONTEXT`) > persisted config (`~/.config/mcpfinder/cli.config.json`, written only by `cluster connect --save`) > default (`:8040` runtime, `:8030` deploy).

Preview the exact commands before executing anything:

```bash
runtime/.venv/bin/python -m runtime.cli --json cluster create --mode k3d --name mcpfinder --dry-run
```

Then bring a cluster up (local host services, or a k3d container cluster):

```bash
runtime/.venv/bin/python -m runtime.cli --json cluster create --mode local --bg
runtime/.venv/bin/python -m runtime.cli --json cluster create --mode k3d --name mcpfinder
```

Point the CLI at an existing cluster and verify reachability of the router `/health` (:8040) and deploy `/health` (:8030). Use `--save` to persist the resolved URLs/context:

```bash
runtime/.venv/bin/python -m runtime.cli --json cluster connect --mode remote \
  --runtime-url http://localhost:8040 \
  --deploy-url http://localhost:8030 \
  --dry-run
runtime/.venv/bin/python -m runtime.cli --json cluster connect --mode k3d \
  --kube-context k3d-mcpfinder \
  --save
```

Aggregate router + deploy health/readiness (k3d mode also runs `kubectl get deploy -l part-of=mcpfinder`):

```bash
runtime/.venv/bin/python -m runtime.cli --json cluster status --mode k3d --dry-run
runtime/.venv/bin/python -m runtime.cli --json cluster status --mode local
```

Teardown is destructive and requires `--yes`. The k3d path refuses non-`mcpfinder`-scoped cluster names unless `--force` is given:

```bash
runtime/.venv/bin/python -m runtime.cli --json cluster down --mode k3d --name mcpfinder --yes --dry-run
runtime/.venv/bin/python -m runtime.cli --json cluster down --mode k3d --name mcpfinder --yes
```

## Deploy an MCP (deploy service)

The `mcp` group drives the **separate deploy service** (default `:8030`, not the router). `mcp deploy` POSTs to `{deploy_url}/deploy` and consumes the returned `text/event-stream`; `mcp list`/`mcp get` read `{deploy_url}/deployments`. `mcp register` is the exception: it registers an already-running manifest directly in the router (`{runtime_url}/manifests` or `/manifests/typed`).

Dry-run first to print the redacted deploy request (env values are redacted on echo) without calling the backend:

```bash
runtime/.venv/bin/python -m runtime.cli --json mcp deploy \
  --repo-url https://github.com/example/my-mcp \
  --name my-mcp \
  --branch main \
  --description "Example MCP" \
  --tag demo --tag example \
  --port 8000 \
  --public \
  --env API_BASE=https://api.example.com \
  --dry-run
```

Then deploy for real (requires `--api-key`/`MCPFINDER_API_KEY`):

```bash
export MCPFINDER_API_KEY="<deploy key>"
runtime/.venv/bin/python -m runtime.cli --json mcp deploy \
  --repo-url https://github.com/example/my-mcp \
  --name my-mcp
```

List and inspect deployments (these support `--dry-run` to print the target URL):

```bash
runtime/.venv/bin/python -m runtime.cli --json mcp list --deploy-url http://localhost:8030
runtime/.venv/bin/python -m runtime.cli --json mcp get my-mcp --deploy-url http://localhost:8030
```

Register an already-running manifest in the router (router `:8040`, not the deploy service):

```bash
runtime/.venv/bin/python -m runtime.cli --json mcp register \
  --runtime-url http://localhost:8040 \
  --file runtime/manifests/demo-sandbox-mcp.yaml \
  --typed \
  --dry-run
```

## Pipelines

The `pipeline` group operates on the router (`:8040`). The v2 templated-YAML engine is the default; v1 named pipelines are selected with `--engine v1`. `pipeline run` is **synchronous** — it blocks until the pipeline finishes and returns the result inline.

List v1 and/or v2 pipelines (`--engine all` is the default):

```bash
runtime/.venv/bin/python -m runtime.cli --json pipeline list --engine all --dry-run
runtime/.venv/bin/python -m runtime.cli --json pipeline list --engine v2
```

Get a pipeline definition; for v1 you can also fetch type-check warnings:

```bash
runtime/.venv/bin/python -m runtime.cli --json pipeline get my-pipeline --engine v2
runtime/.venv/bin/python -m runtime.cli --json pipeline get my-pipeline --engine v1 --type-check
```

Deploy a pipeline definition from a local file (v2 -> `POST /v2/pipelines/deploy`; v1 -> `POST /pipelines/register`). Dry-run first to print the target and redacted definition:

```bash
runtime/.venv/bin/python -m runtime.cli --json pipeline deploy \
  --file runtime/pipelines/v2/my-pipeline.yaml \
  --engine v2 \
  --dry-run
runtime/.venv/bin/python -m runtime.cli --json pipeline deploy \
  --file runtime/pipelines/v2/my-pipeline.yaml \
  --engine v2 \
  --api-key "$MCPFINDER_API_KEY"
```

Run a pipeline synchronously. Inputs come from `--inputs` (JSON string) or `--inputs-file`:

```bash
runtime/.venv/bin/python -m runtime.cli --json pipeline run \
  --name my-pipeline \
  --engine v2 \
  --inputs '{"customer_id":"cust_123"}' \
  --dry-run
runtime/.venv/bin/python -m runtime.cli --json pipeline run \
  --name my-pipeline \
  --inputs '{"customer_id":"cust_123"}' \
  --api-key "$MCPFINDER_API_KEY"
```

Hot-reload pipeline definitions on the runtime host:

```bash
runtime/.venv/bin/python -m runtime.cli --json pipeline reload --dry-run
runtime/.venv/bin/python -m runtime.cli --json pipeline reload --api-key "$MCPFINDER_API_KEY"
```

## Workflows (pipelines + jobs)

A workflow is a **CLI facade over pipelines + jobs** — there is no separate workflow primitive in the runtime (see `contract.workflow_model`). The decision is simple and stated honestly:

- **pipeline** = the definition plus **synchronous** execution (`pipeline run` -> `POST /v2/pipelines/run` or `POST /pipelines/{name}/run`).
- **workflow** = the same definition executed as a tracked, cancelable, pollable **async job** (`workflow run` -> `POST /jobs`, returning a `job_id`).

Use `pipeline run` when you want the result inline; use `workflow run` when you want durability, polling, and cancellation.

Scaffold a pipeline definition file locally (pure-local, no network; v2 default). Each `--step` is an `mcp.tool` token:

```bash
runtime/.venv/bin/python -m runtime.cli --json workflow create \
  --name my-workflow \
  --engine v2 \
  --step demo-sandbox-mcp.get_demo_customer \
  --output runtime/pipelines/v2/my-workflow.yaml \
  --dry-run
runtime/.venv/bin/python -m runtime.cli --json workflow create \
  --name my-workflow \
  --step demo-sandbox-mcp.get_demo_customer
```

Deploy the scaffolded definition (identical to `pipeline deploy`):

```bash
runtime/.venv/bin/python -m runtime.cli --json workflow deploy \
  --file runtime/pipelines/v2/my-workflow.yaml \
  --engine v2 \
  --dry-run
```

Run it as a durable async job. This submits `POST /jobs` and returns a `job_id`:

```bash
runtime/.venv/bin/python -m runtime.cli --json workflow run \
  --name my-workflow \
  --inputs '{"customer_id":"cust_123"}' \
  --job-name nightly-demo \
  --dry-run
runtime/.venv/bin/python -m runtime.cli --json workflow run \
  --name my-workflow \
  --inputs '{"customer_id":"cust_123"}' \
  --api-key "$MCPFINDER_API_KEY"
```

Poll a job by id, or list jobs (optionally filtered by status):

```bash
runtime/.venv/bin/python -m runtime.cli --json workflow status --job-id <job_id> --dry-run
runtime/.venv/bin/python -m runtime.cli --json workflow status --list --status running
```

Cancel a running job (`POST /jobs/{job_id}/cancel`):

```bash
runtime/.venv/bin/python -m runtime.cli --json workflow cancel --job-id <job_id> --dry-run
runtime/.venv/bin/python -m runtime.cli --json workflow cancel --job-id <job_id> --api-key "$MCPFINDER_API_KEY"
```

## End-to-end smoke

`smoke zero-to-hero` exercises the full path across the deploy service (`:8030`) and runtime router (`:8040`) for public-preview readiness. Dry-run prints the exact check list it would run, with no network calls:

```bash
runtime/.venv/bin/python -m runtime.cli --json smoke zero-to-hero \
  --deploy-url http://localhost:8030 \
  --runtime-url http://localhost:8040 \
  --dry-run
```

Then run it live (requires `--api-key`/`MCPFINDER_API_KEY`):

```bash
runtime/.venv/bin/python -m runtime.cli --json smoke zero-to-hero \
  --deploy-url http://localhost:8030 \
  --runtime-url http://localhost:8040 \
  --api-key "$MCPFINDER_API_KEY"
```

If either service is down, the smoke exits non-zero with `backend_unavailable` instead of reporting fake success.

## Full e2e harness (public-preview validation)

`scripts/cli_e2e.sh` runs the entire agent-facing CLI surface — read, execute, the workflow async-job lifecycle, control-plane export, and the negative/fail-honestly paths — against a live local instance, with per-check pass/fail accounting. Use it to validate a fresh local install before public preview or in CI:

```bash
# Requires a running local cluster (router :8040, deploy :8030) and an
# agent-operator key. The seeded local-dev key from
# scripts/001_create_api_keys.sql has the needed action_permissions.
MCPFINDER_API_KEY="$MCPFINDER_API_KEY" ./scripts/cli_e2e.sh
```

Every check asserts an exit code (`0` for success paths, `2` for the fail-honestly paths), so a non-zero overall exit means the local platform is not preview-ready. Idle MCPs are tolerated via a 60s invoke timeout for scale-from-zero cold starts.

### Agent execution requires an operator key

`POST /call` (invoke) works with any active key, but running pipelines, submitting jobs/workflows, exporting the registry, and registering MCPs are gated by `api_keys.action_permissions` (see `runtime/router.py:_authorize_action`). The seeded local-dev key grants a least-privilege operator set — `pipeline.invoke`, `agent.invoke`, `agent.register`, `mcp.server.register`, `registry.export`, `registry.import` — and deliberately excludes privileged actions (`policy.admin`, `credential.*`, `sealed_handle.*`, `audit.read`). To enable execution on an existing database, re-apply `scripts/001_create_api_keys.sql`.

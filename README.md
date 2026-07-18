# Sealfleet

An open-source MCP Agent Platform — discover, connect, and securely execute agent-callable tools using Model Context Protocol.

## Overview

Sealfleet lets organizations expose internal/external capabilities as agent-callable tools. LLMs plan and orchestrate; a secure execution layer handles secrets, credentials, and sensitive inputs. Everything is observable, auditable, and production-ready.

**Building your own MCP?** Start with the [Build your own MCP](docs/BUILD_YOUR_OWN_MCP.md) guide — author a tool, describe it with a manifest, deploy it, and invoke it through the router/CLI. The platform is for exposing *your* capabilities; `mcps/demo_sandbox/` is a copy-ready starting point.

## Architecture

- **runtime/** — Runtime Router: MCP server framework, tool invoke (`/call`), typed/named/v2 pipelines, async jobs, manifests, scale-to-zero, and the `mcpfinder` CLI
- **deploy/** — Deploy service: git-to-Kubernetes MCP deployment
- **registry/** — Discovery service + federation
- **core-agent/** — LLM-powered agent that maps natural language to pipeline execution
- **broker/** — Secrets integration + token minting + signing
- **policy/** — Policy decision point + policy packs
- **observability/** — OpenTelemetry wiring + audit log schema (see `docs/OBSERVABILITY.md` for receipt map, OTEL backend config, readiness, and smoke checks)
- **packages/mcpfinder-auth/** — Shared enterprise identity/compliance contract
- **portal/** — Web UI: docs, catalog, test console, deploy UI, and sealed-input/approval flows
- **mcps/** — Example MCP servers (demo sandbox, weather trip planner)

## Licensing

Sealfleet is **open-core**. The platform is free forever under Apache-2.0 — run
it, modify it, self-host it, unlimited. A set of **enterprise features** ships
in the same codebase and unlocks with a license:

| | Free (Community) | Enterprise |
|---|---|---|
| Tools, pipelines, jobs, portal, audit, sealed credentials | ✅ | ✅ |
| Users | 1 (local login) | Unlimited |
| **SSO / OIDC / SAML + IdP group→role mapping** | — | ✅ |
| **Multi-tenant, SCIM provisioning, advanced RBAC, audit export** | — | ✅ |

See **[LICENSING.md](LICENSING.md)** for the full matrix and how it works.

### Sealfleet Enterprise

Enterprise unlocks single sign-on, multi-user/multi-tenant, SCIM, and advanced
RBAC/audit — via a license key or an **AWS Marketplace** subscription. Apply a
key by setting `SEALFLEET_LICENSE_KEY` (or `licensing.licenseKey` in Helm), then
check `GET /license`.

**Get a license / talk to us:** 
sales@sealfleet.ebdsweden.com · or open a [GitHub discussion](https://github.com/EBD-Sweden/sealfleet/discussions).


## Deploying

- **Local eval:** `docker compose up --build` (below).
- **Your own cloud (BYOF):** one `terraform apply` provisions EKS/GKE + managed
  Postgres + secrets + ingress in **your** AWS or GCP account.
- **Existing cluster:** `helm install` the chart with your managed Postgres.

Full guide with prerequisites, secrets, TLS, upgrade/teardown, and a production
checklist: **[docs/DEPLOY.md](docs/DEPLOY.md)**.

## Quick Start

### Run the whole stack in one command (Docker Compose)

The fastest path — clone and bring up the entire platform (Postgres + registry + deploy
service + runtime router + portal), with migrations and seeds applied automatically:

```bash
git clone https://github.com/EBD-Sweden/sealfleet.git
cd mcpfinder
docker compose up --build
```

Then:
- **Portal (UI):** http://localhost:3004 — log in as **`admin@sealfleet.io`** with the
  `ADMIN_INITIAL_PASSWORD` from `docker-compose.yml` (default `admin`; set only on first
  boot, change it after logging in).
- **Runtime API:** http://localhost:8040 (`GET /health`)
- A **live demo MCP** (`demo-sandbox-mcp`, fake data only) is started so the catalog has an
  invocable tool out of the box — try it in the portal test console or via
  `POST /call` with `{"mcp": "demo-sandbox-mcp", "tool": "summarize_fake_invoice", ...}`.
- A **Weather Trip Planner example** (portal → *Weather Example*) shows the core loop
  end-to-end: the `weather_trip_planner` pipeline gathers each city's past week +
  next 10 days of real weather (key-less Open-Meteo/met.no APIs via `weather-trip-mcp`),
  ranks the cities against your preferences (sunshine, ~27°C, low wind), and the page
  visualizes the result. Use it as the template for your own pipeline → dashboard pages.
- A **seeded local-dev operator API key** is created automatically
  (`scripts/001_create_api_keys.sql`) so agents/CLI can invoke tools and run pipelines
  out of the box — e.g. `MCPFINDER_API_KEY=<key> python -m runtime.cli status`.

**Connect your own IdP (Keycloak, Okta, Azure AD, Auth0, …)** in the portal under
**Admin → Tenants** as the admin user:
1. Create a tenant for your organization, enable SSO, and set OIDC `issuer`,
   `client_id`, `client_secret`, `scopes`, and your email domain(s) under allowed domains.
2. Register `http://localhost:3004/login/sso/callback` as a redirect URI in your IdP
   (replace the host with your portal URL if deployed elsewhere — it follows `NEXTAUTH_URL`).
3. Map IdP group claims → roles under the tenant's SSO role mappings (e.g. claim
   `groups=engineers` → role `engineer`); roles are assigned automatically at login.

Users then sign in from the login page with their work email and are routed to your IdP.
See [`AUTH_PORTAL.md`](AUTH_PORTAL.md) / [`AUTH_BACKEND.md`](AUTH_BACKEND.md) and
[`docs/MCPFINDER_CLI.md`](docs/MCPFINDER_CLI.md) for details.

> Defaults are **local/dev**: compose generates persistent RS256 signing keys, a NextAuth
> secret, and an encryption key into the `sealfleet_keys` volume on first boot, so logins
> and portal↔router trust survive restarts. For production, provide your own
> `ROUTER_RS256_PRIVATE_KEY`, `NEXTAUTH_RS256_PRIVATE_KEY`, `ENCRYPTION_KEY`,
> `NEXTAUTH_SECRET`, a strong DB password and `ADMIN_INITIAL_PASSWORD`, and enable TLS
> (see `k8s/tls/`). Build your own MCPs with
> [`docs/BUILD_YOUR_OWN_MCP.md`](docs/BUILD_YOUR_OWN_MCP.md).

### Local-from-source (no Docker)

Sealfleet public-test means an open-source clone-and-run release: evaluators should be able to run the repository themselves with fake demo data and no private credentials. Hosted/k3d smoke remains QA evidence, not the primary public-test path.

### 1. Clone and prepare a local checkout

```bash
git clone https://github.com/EBD-Sweden/sealfleet.git
cd mcpfinder
python3 -m venv runtime/.venv
runtime/.venv/bin/python -m pip install -r runtime/requirements.txt -r registry/requirements.txt -r deploy/requirements.txt
cd portal && env -u npm_config_prefix npm install && cd ..
```

Expected result: Python dependencies install into `runtime/.venv/` and portal dependencies install under `portal/node_modules/`. No secrets are required for the deterministic fake demos below.

### 2. Run the deterministic fake OpenAPI-to-MCP demo

```bash
runtime/.venv/bin/python scripts/demo-openapi-to-mcp.py --invoke
```

Expected result: the command prints a JSON receipt with `mode=public_demo`, `tenant_id=demo-sandbox`, `workspace_id=demo-external-evaluation`, generated artifacts under ignored local scratch space at `runtime/.generated/demo-fake-crm-mcp/`, and an invocation for `get_demo_customer` using `CUST-DEMO-001`. It performs no network calls, reads no credentials, does not deploy anything, and does not modify the checked-in fixture under `runtime/generated/demo-fake-crm-mcp/`.

### 2b. Run the Sealfleet CLI smoke path

Sealfleet CLI is the project-specific Command Line Interface for agents/operators: it validates `mcpfinder.cli.config/v1`, checks runtime health, builds `/call` invocation payloads, and calls registry/manifest control-plane APIs. It is intentionally Sealfleet-only and rejects Aether/OpenSnow config bleed.

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

Expected result: contract, invoke dry-run, and local-demo smoke dry-run output are deterministic, require no backend and no secrets, and the focused CLI acceptance tests pass. Live control-plane commands (`registry export/import`, `manifest list/get/register`) require a real router/API key where protected and fail with structured `backend_unavailable`/`auth_missing` errors instead of success-looking stubs. Full quickstart: [`docs/MCPFINDER_CLI.md`](docs/MCPFINDER_CLI.md).

### 2c. Agent zero-to-hero path (CLI)

For local LLM agents and operators, `python -m runtime.cli` is the canonical zero-to-hero path: stand up a cluster, deploy an MCP through the separate deploy service (`:8030`), run pipelines synchronously, and run workflows as durable async jobs against the router (`:8040`). Every command below supports `--json` and `--dry-run`; secrets are always redacted, and control-plane/cluster ops fail with structured errors and a non-zero exit when a backend or tool is unreachable. The sequence is dry-run-first so it is safe to copy-paste before any backend is live:

```bash
# 1. Provision a local cluster (host services; use --mode k3d for a k3d cluster)
runtime/.venv/bin/python -m runtime.cli --json cluster create --mode local --dry-run
# 2. Point the CLI at it and verify router + deploy reachability (--save persists URLs)
runtime/.venv/bin/python -m runtime.cli --json cluster connect --mode local --save --dry-run
# 3. Deploy an MCP from a git repo via the deploy service (:8030)
runtime/.venv/bin/python -m runtime.cli --json mcp deploy \
  --repo-url https://github.com/EBD-Sweden/example-mcp --name example-mcp --dry-run
# 4. Run a pipeline synchronously (router :8040; v2 templated YAML is the default engine)
runtime/.venv/bin/python -m runtime.cli --json pipeline run \
  --name example-pipeline --inputs '{"customer_id":"cust_123"}' --dry-run
# 5. Run the same definition as a durable, pollable, cancelable async job (POST /jobs)
runtime/.venv/bin/python -m runtime.cli --json workflow run \
  --name example-pipeline --inputs '{"customer_id":"cust_123"}' --dry-run
# 6. End-to-end readiness smoke across deploy (:8030) + runtime (:8040)
runtime/.venv/bin/python -m runtime.cli --json smoke zero-to-hero --dry-run
```

Pipeline vs. workflow is an honest facade, not two server primitives: a *pipeline* is the definition plus synchronous execution (`pipeline run`), while a *workflow* is that same definition executed as a tracked async **job** — `workflow run` maps to `POST /jobs` and returns a `job_id` you poll with `workflow status` and stop with `workflow cancel`. Drop `--dry-run` to execute against a live backend (deploy/run commands then require `--api-key` or `MCPFINDER_API_KEY`). Full command/flag reference: [`docs/MCPFINDER_CLI.md`](docs/MCPFINDER_CLI.md).

### 3. Optional local service mode

For full local service smoke, start PostgreSQL on `localhost:54323` with database `mcpfinder` and user `admin`; supply the local password through your shell environment, then run migrations and demo seed:

```bash
export PGPASSWORD="${PGPASSWORD:?set your local Postgres password}"
for f in db/migrations/*.sql; do
  psql -h localhost -p 54323 -U admin -d mcpfinder -f "$f"
done
psql -h localhost -p 54323 -U admin -d mcpfinder -f db/seeds/010_demo_sandbox.sql
./scripts/start-local.sh
./scripts/start-local.sh --status
```

Expected result: registry `:8010`, deploy `:8030`, router `:8040`, and portal `:3000` report healthy/running. Stop them with:

```bash
./scripts/start-local.sh --stop
```

### 4. Optional Kubernetes/k3d QA smoke

Kubernetes public-test manifests are secret-ref only: top-level `k8s/*.yaml` consume DB URLs, auth/session secrets, encryption keys, and provider credentials through Kubernetes `Secret` references. Docker stdio and ephemeral API keys are disabled by default in the public-test path. Required Secret objects/keys are referenced directly in the `k8s/*.yaml` manifests.

Use k3d/k8s smoke as operator evidence after secrets are supplied, not as the first OSS user journey:

```bash
NAMESPACE=demo-sandbox DRY_RUN=1 scripts/k8s-demo-smoke.sh
NAMESPACE=demo-sandbox DRY_RUN=0 scripts/k8s-demo-smoke.sh --cleanup
```

### 5. Verification commands

```bash
runtime/.venv/bin/python -m pytest runtime/tests packages/mcpfinder-auth/tests -q
(cd portal && env -u npm_config_prefix npm test -- --run)
(cd portal && env -u npm_config_prefix npm run lint)
```

Expected baseline from the latest QA pass: runtime/auth tests pass, portal Vitest passes, and portal lint exits 0 with warnings only.

Detailed self-hosted walkthrough: [`docs/EXTERNAL_DEMO_QUICKSTART.md`](docs/EXTERNAL_DEMO_QUICKSTART.md).

A P1 gRPC creation spike now lives in `runtime/grpc_reflection_importer.py`: it is disabled by default, requires authenticated caller identity before descriptor import/reflection discovery, redacts auth metadata, converts unary RPC descriptors to typed MCP manifests, and records streaming RPCs as unsupported rather than production-ready tools. Live reflection is not a public/anonymous endpoint: auth metadata is rejected unless the caller injects a secure gRPC channel, and explicitly enabled local/dev insecure reflection is allowed only without metadata. TLS/mTLS channel construction and streaming execution remain outside this spike.

A P1 external-agent gateway parity slice is available through `POST /external-agents`: tenant admins can register a JSON-RPC HTTP agent as `agent:<name>.invoke` with bearer auth referenced only by sealed handle. Invocation goes through runtime auth, tenant ownership, policy/rate-limit hooks, 50ms..10s timeout bounds, and redacted audit; manifest catalog views are tenant-filtered, cross-tenant name overwrite is rejected, named pipelines and background `/jobs`/`/jobs/batch` must satisfy `agent.invoke` before external-agent network egress, and job tenants are bound to the authenticated request tenant unless a platform/global authority submits for another tenant. Broad agent marketplace and production OpenAI/Anthropic integrations remain non-goals until credential-model approval.

## Production Launch Plan

Sealfleet is now in platform hardening + transport flexibility, focused on production readiness across security boundaries, observability, and deployment.

An enterprise identity/compliance layer is available for managed/hosted deployments. The first implementation slice is now live in `packages/mcpfinder-auth/src/mcpfinder_auth/enterprise.py` and exposed by the runtime at `GET /enterprise/contract`, covering organization/team/service-identity models, OIDC/SAML/SCIM descriptors, RBAC/ABAC policy envelopes, `audit-event/v1`, sealed secret/session descriptors, marketplace identity hooks, and Sealfleet resource adapters. The runtime router now enforces action-level gates for sensitive enterprise routes (`audit.read`, `policy.admin`, `mcp.server.register`, `credential.*`, `sealed_handle.*`) and includes SCIM user/group lifecycle endpoints for deactivation/session revocation and group-role mapping.

## Kubernetes MCP networking contract

New MCP deployments must use Kubernetes service DNS for MCP-to-MCP traffic.

- internal MCP endpoint pattern: `http://<service-name>.<namespace>.svc.cluster.local:<port>`
- service type for MCPs: `ClusterIP`
- do not use pod IPs, ClusterIP addresses directly, NodePorts, or `localhost` for internal service discovery
- use `host.k3d.internal` only for host-local dependencies

See `docs/K8S_SERVICE_DISCOVERY.md` and `docs/PIPELINE_ROUTING_GUARDRAILS.md`.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for the full text.

Copyright 2026 EBD Sweden AB.

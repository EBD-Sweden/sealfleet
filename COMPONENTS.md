# Components Index

## Status Legend
- ⏳ Not started
- 🚧 In progress
- ✅ MVP complete
- 🎯 Production-ready

---

## `runtime/` — Runtime Router
**Status:** ✅ MVP complete
**Purpose:** MCP server framework — expose tools, run pipelines, enforce auth/policy/tracing
**Language:** Python
**Dependencies:** FastAPI, PostgreSQL, OpenTelemetry

**Modules:**
- `runtime/router.py` — Core FastAPI router: `/call` invoke, `/pipelines/*` + `/v2/pipelines/*`, async `/jobs`, manifests, registry, scale-to-zero, action-level auth gates ✅
- `runtime/cli.py` — `python -m runtime.cli` agent/operator CLI (status, invoke, pipelines, workflows, deploy, registry) ✅
- `runtime/credentials.py` — Credential resolution (BYOK / platform Fernet / k8s Secret modes) ✅
- `runtime/policy_hooks.py` — Policy enforcement hooks ✅
- `runtime/manifests/`, `runtime/pipelines/`, `runtime/types.yaml`, `runtime/channels.yaml` — Typed manifests, named/v2 pipelines, channel/type definitions ✅

**Key interfaces:**
- `POST /call` — Invoke an MCP tool through the router
- `POST /v2/pipelines/run` / `POST /jobs` — Synchronous pipeline run / durable async job
- `GET /enterprise/contract` — Enterprise identity/compliance contract

---

## `registry/` — Discovery Service
**Status:** ✅ MVP complete
**Purpose:** Central catalog of MCP servers and tools
**Language:** Python
**Dependencies:** SQLite, FastAPI

**Modules:**
- `registry/server.py` — FastAPI discovery API (CRUD + search) ✅
- `registry/storage.py` — SQLite-backed tool/server metadata storage ✅
- `registry/federation.py` — Cross-org federation (Phase 2+) ⏳

**Key interfaces:**
- `POST /servers` — Register an MCP server
- `GET /tools?q=` — Search/list available tools
- `POST /servers/{id}/tools` — Register a tool under a server

---

## `portal/` — Web UI (incl. private input flows)
**Status:** ✅ MVP complete
**Purpose:** Docs, catalog, test console, deploy UI, and collecting sensitive data outside LLM context
**Language:** TypeScript + React (Next.js)

**Modules:**
- `portal/src/components/SecureInputModal.tsx` — Sealed input field with handle generation ✅
- Test console, catalog, docs, and deploy UI pages under `portal/src/app/` ✅

---

## `broker/` — Secrets & Credential Broker
**Status:** ✅ MVP complete
**Purpose:** Manage credentials, mint tokens, handle signing
**Language:** Python
**Dependencies:** In-memory MVP (later: HashiCorp Vault, KMS, HSM)

**Modules:**
- `broker/vault.py` — Secret storage with ownership and expiry ✅
- `broker/tokens.py` — HMAC-based short-lived token minting ✅
- `broker/signer.py` — Simulated crypto transaction signing ✅

**Key interfaces:**
- `vault.store_secret(owner, name, value)` — Store a credential
- `vault.inject_credentials(user, tool)` — Get creds for tool execution
- `token_minter.mint(user, scope, ttl)` — Mint scoped access token
- `signer.sign_transaction(user, chain, tx)` — Sign a transaction

---

## `policy/` — Policy Engine
**Status:** ✅ MVP complete
**Purpose:** Centralized allow/deny/redact decisions
**Language:** Python

**Modules:**
- `policy/engine.py` — Priority-based policy evaluation engine ✅
- `policy/packs/default.yaml` — Default access control rules ✅
- `policy/packs/crypto_trading.yaml` — Crypto-specific policy pack ✅

**Key interfaces:**
- `engine.load_pack(yaml_path)` — Load rules from YAML
- `engine.evaluate(context)` — Evaluate policy decision

---

## `observability/` — Tracing & Audit
**Status:** ✅ MVP complete
**Purpose:** Distributed tracing + immutable audit logs
**Language:** Python
**Dependencies:** SQLite (audit), in-memory (tracing)

**Modules:**
- `observability/tracing.py` — Tracing provider with pluggable exporters ✅
- `observability/audit.py` — SQLite-backed immutable audit log ✅
- `docs/OBSERVABILITY.md` — Trace/audit emission map, OTEL backend configuration, redaction boundaries, readiness matrix, and operator smoke script usage ✅

**Key interfaces:**
- `tracer.span(operation)` — Context manager for trace spans
- `audit.record(user, action, resource, result)` — Log audit event
- `audit.query(filters)` — Query audit trail

---

## `mcps/` — Example MCP Servers
**Status:** ✅ MVP complete
**Purpose:** Reference MCP servers demonstrating end-to-end flows
**Language:** Python

**Modules:**
- `mcps/demo_sandbox/` — Deterministic fake-data sandbox for external testers ✅

---

## `core-agent/` — LLM-Powered Core Agent
**Status:** ✅ MVP complete
**Purpose:** Reference agent that discovers runtime capabilities, uses LLM to map natural language questions to output_type+inputs, executes via /resolve, and returns formatted answers
**Language:** Python
**Dependencies:** FastAPI, httpx, openai, pyyaml
**Port:** 8050

**Modules:**
- `core-agent/agent.py` — FastAPI service with /health, /capabilities, /ask endpoints + CLI mode ✅
- `core-agent/mcp.yaml` — Self-describing MCP manifest ✅
- `core-agent/requirements.txt` — Python dependencies ✅
- `core-agent/.env` — Configuration (runtime URL, LLM proxy, model) ✅

**Key interfaces:**
- `GET /health` — Health check
- `GET /capabilities` — Proxies runtime /capabilities
- `POST /ask` — Natural language question → LLM reasoning → /resolve → formatted answer

---

## `docs/` — Documentation
**Status:** ⏳ Not started
**Purpose:** User guides, API docs, deployment guides

---

## `runtime/cli.py` — MCP server CLI
**Status:** ✅ MVP complete
**Purpose:** Canonical project-scoped MCP server command/runtime interface that lets local LLM agents go zero-to-hero: cluster lifecycle, MCP deploy, pipeline + workflow execution, plus the original agent/operator validation, runtime status/readiness, MCP invocation, manifest/service registry operations, registry import/export, and local demo smoke checks. All new command groups support `--json` and `--dry-run`, redact secrets, and fail honestly (structured error + non-zero exit) when a backend/control-plane is unreachable.
**Language:** Python stdlib
**Entrypoint:** `python -m runtime.cli`
**Dependencies:** runtime router (`:8040`), deploy service (`:8030`), and `k3d`/`kubectl`/`docker` for cluster ops (shelled out, fail honestly if absent)

**Modules:**
- `runtime/cli.py` — canonical `python -m runtime.cli` argparse CLI with structured JSON output, config validation, dry-run `/call` and local-demo smoke payloads, live runtime HTTP calls, secret redaction, and honest non-zero backend/auth failures ✅
- `scripts/mcpfinder_cli.py` — compatibility wrapper that delegates to `runtime.cli` ✅
- `scripts/mcpfinder-cli` — executable compatibility wrapper for shell users ✅
- `runtime/tests/test_mcpfinder_cli.py` — deterministic acceptance tests for module/script entrypoints, global `--json`, invoke payload handling, backend-unavailable failures, project-scope validation, secret redaction, smoke dry-run, and contract surface ✅
- `.github/workflows/cluster-routing-guard.yml` — runs CLI acceptance tests in CI alongside routing guard ✅
- `docs/MCPFINDER_CLI.md` — quickstart and smoke commands ✅

**Key interfaces:**
- `contract` — prints `mcpfinder.cli.contract/v1` and canonical `python -m runtime.cli` entrypoint
- `validate --config` — validates `mcpfinder.cli.config/v1` and rejects other products bleed
- `status` — calls real runtime `GET /health` and `GET /ready`
- `invoke --dry-run` — prints exact `POST /call` request without backend/secrets; live invoke requires API key
- `manifest list/get/register` — maps to `GET /manifests`, `GET /manifests/{name}`, `POST /manifests`, and `POST /manifests/typed`
- `registry export/import` — maps to `GET /registry/export` and `POST /registry/import?dry_run=<bool>`
- `smoke local-demo` — checks health, readiness, manifest listing, and demo-sandbox tool invocation; `--dry-run` prints those operations without fake success
- `smoke zero-to-hero` — end-to-end public-preview readiness across the deploy service (`/health`, `/ready`, `/deployments`) and runtime router (`/health`, `/ready`, `/manifests`, `/pipelines`, `/v2/pipelines`); `--dry-run` prints the exact check list; accepts `--deploy-url`/`--runtime-url`

**Command groups (expanded surface):**
- `cluster create|connect|status|down` — local host services (`scripts/start-local.sh`) or k3d (`k3d cluster create` + `kubectl apply -k k8s/dev-local/`); `connect --save` persists URLs/context to `~/.config/mcpfinder/cli.config.json` after verifying `/health` on router `:8040` + deploy `:8030`; `status --mode k3d` also runs `kubectl get deploy -l part-of=mcpfinder`; `down` is destructive (requires `--yes`) and refuses non-`mcpfinder`-scoped k3d names without `--force`
- `mcp deploy|list|get` (via the SEPARATE deploy service, default `:8030`) — `deploy` POSTs `{deploy_url}/deploy` and consumes the SSE stream (requires `--api-key`/`MCPFINDER_API_KEY` for live), `list`→`GET /deployments`, `get NAME`→`GET /deployments/{name}`
- `mcp register --file [--typed]` — registers an already-running manifest directly in the router (`POST {runtime_url}/manifests` or `/manifests/typed`)
- `pipeline list|get|deploy|run|reload` (router `:8040`; v2 templated YAML is the default engine, v1 named via `--engine v1`) — `list --engine {v1,v2,all}`, `get NAME [--type-check]` (v1 type-check via `/pipelines/{name}/type-check`), `deploy --file` (v2 `POST /v2/pipelines/deploy`, v1 `POST /pipelines/register`), `run --name` SYNCHRONOUS (v2 `POST /v2/pipelines/run`, v1 `POST /pipelines/{name}/run`), `reload`→`POST /pipelines/reload`
- `workflow create|deploy|run|status|cancel` — facade over pipelines + jobs (no separate workflow primitive in the runtime). `create` scaffolds a pipeline definition file locally (pure-local, v2 default), `deploy` is identical to `pipeline deploy`, `run --name` submits a durable ASYNC job (`POST /jobs` → returns `job_id`), `status [--job-id | --list]`→`GET /jobs/{id}` or `GET /jobs`, `cancel --job-id`→`POST /jobs/{id}/cancel`

**Workflow-vs-pipeline (facade design):** `pipeline` = the definition + synchronous execution; `workflow` = the same definition executed as a tracked, cancelable, pollable async JOB. The CLI invents no server concept — `workflow run` maps to `POST /jobs`.

**Config (`mcpfinder.cli.config/v1`):** required keys unchanged (`schema`, `product`, `runtime_url`, `allowed_scopes`); optional `deploy_url`, `kube_context`, `cluster_mode` (`local|k3d|remote`); new env `MCPFINDER_DEPLOY_URL`, `MCPFINDER_KUBE_CONTEXT`; new `allowed_scopes` values `deploy`, `cluster`. URL resolution order: flag > env > persisted config (written only by `cluster connect --save`) > default.

---

## `portal/` — Dashboard Portal
**Status:** ✅ MVP complete
**Purpose:** Next.js dashboard for managing MCP servers, tools, audit, and docs
**Language:** TypeScript (Next.js + Tailwind + shadcn/ui)
**Dependencies:** next, react, tailwindcss, shadcn/ui, lucide-react

**Pages:**
- `/` — Home dashboard with stats cards and recent activity ✅
- `/catalog` — Grid of MCP server cards ✅
- `/catalog/[id]` — Server detail + tools list + "Try it" modal ✅
- `/discover` — Community MCP servers listing ✅
- `/docs` — Tabbed docs (What is Sealfleet / Create your MCP / Security model) ✅
- `/audit` — Table of trace/audit events ✅
- `/deploy` — Git-to-K8s deploy pipeline with SSE log streaming ✅
- `/pipeline` — Secure runtime pipeline visualizer + runner ✅
- `/ask` — Chat interface to core agent (LLM-powered Q&A) ✅

**Key files:**
- `src/auth.ts` — NextAuth providers, JWT callbacks, authoritative SSO role sync
- `src/lib/admin-auth.ts` — Server-side platform_admin/tenant_admin capability resolver and tenant-scope helpers; tenant admin roles must match the session tenant, and platform_admin roles must be platform-scoped
- `src/app/api/admin/*` — Tenant-filtered admin APIs; platform routes require platform_admin
- `src/lib/mock-data.ts` — All mock data (servers, tools, audit events, stats)
- `src/components/app-sidebar.tsx` — Fixed left sidebar navigation
- `src/app/layout.tsx` — Root layout with sidebar + top bar

---

## `deploy/` — Git-to-K8s Deploy Service
**Status:** ✅ MVP complete
**Purpose:** Clone Git repos, build Docker images, deploy to k3d, register in Sealfleet
**Language:** Python
**Dependencies:** FastAPI, uvicorn, sse-starlette, psycopg2, pyyaml

**Modules:**
- `deploy/server.py` — FastAPI service with SSE streaming deploy pipeline ✅
- `deploy/requirements.txt` — Python dependencies ✅

**Key interfaces:**
- `GET /health` — Health check
- `POST /deploy` — Deploy from Git (SSE stream)
- `GET /deployments` — List all deployments
- `GET /deployments/{name}` — Single deployment status

**Pipeline steps:** Clone → Detect config → Docker build+push → K8s manifests → kubectl apply → Auto-register typed manifest in runtime → DB register

---

## `runtime/` — Secure Message Runtime Router
**Status:** ✅ MVP complete
**Purpose:** Kernel layer between agents and MCP tools. Named channel routing with policy enforcement and audit.
**Language:** Python
**Dependencies:** FastAPI, uvicorn, sse-starlette, psycopg2-binary, pyyaml, httpx

**Modules:**
- `runtime/router.py` — FastAPI runtime router (port 8040); validates API keys plus portal RS256 session JWTs via JWKS/configured public key with optional issuer/audience enforcement; preserves portal JWT action/group claims, loads DB-backed API-key action metadata from `api_keys.action_permissions`, requires explicit durable `allow_identity_delegation`/metadata or configured SHA-256 fingerprint before trusting portal delegated identity headers, and enforces fail-closed enterprise action RBAC for audit/policy/manifest/registry import-export/pipeline-invoke/external-agent/sealed/credential/SCIM endpoints; exposes tenant-scoped external agents as `agent:<name>` MCP tools with sealed auth handles, tenant ownership, tenant-filtered manifest visibility, cross-tenant name-collision protection, policy/rate-limit/audit hooks, background-job `agent.invoke` enforcement plus authenticated tenant binding, and no raw token/prompt leakage; stores structured audit events with first-class tenant scope and tenant-filters `/audit/events` unless explicit platform/global audit authority is present; rejects SCIM same-email cross-tenant upserts; stores sealed handles with strict tenant/subject ownership, single-use in-process resolve, redacted resolve success/denial audit events, metadata-only HTTP surfaces, `sealed_handle.delete` authorization for deletion, and fail-closed production encryption-key handling; legacy HS256 fallback shares the same configured issuer/audience checks ✅
- `runtime/policy_hooks.py` — Config-driven runtime hook boundary for pre-call/post-call MCP execution phases; includes deterministic `HookManager`, `OutputLengthGuard`, `SecretsPiiGuard`, and the always-on `ManifestPiiGuard` (redacts manifest-declared `pii_fields` output paths from every tool result, audited by field name only) for HTTP + Docker stdio parity with redacted hook audit events ✅
- `runtime/grpc_reflection_importer.py` — Disabled-by-default gRPC reflection-to-MCP manifest importer spike; converts authenticated local/live protobuf descriptors into unary-only typed manifests, redacts auth metadata, and records unsupported streaming methods without advertising them as tools 🚧
- `runtime/channels.yaml` — Channel policy definitions ✅
- `runtime/manifests/*.yaml` — MCP manifest configs, including `demo-sandbox-mcp.yaml` fake-data external demo manifest ✅
- `runtime/pipelines/v2/demo_sandbox_invoice_review.yaml` — Executable v2 demo pipeline with tenant/workspace quota enforcement hooks ✅
- `mcps/demo_sandbox/main.py` — Deterministic fake-data demo MCP with no file/network/secret access ✅
- `mcps/weather_trip/main.py` — Public example MCP: real daily weather (Open-Meteo primary, met.no fallback, no API keys) + deterministic city ranking for trip preferences; backs the `weather_trip_planner` v2 pipeline and the portal `/weather-trip` dashboard (the reference pipeline → visualization example) ✅
- `runtime/pipelines/v2/weather_trip_planner.yaml` — Example v2 pipeline (fetch cities' weather → rank) consumed by the portal weather dashboard ✅
- `portal/src/app/weather-trip/page.tsx` + `portal/src/app/api/weather-trip/route.ts` — Example dashboard that runs the pipeline through the session-gated portal proxy and visualizes ranking, per-day scores, and temperature sparklines ✅
- `k8s/demo-sandbox-mcp.yaml` — Demo namespace/deployment/service plus egress-deny NetworkPolicy ✅
- `scripts/k8s-demo-smoke.sh` — Demo-scoped dry-run cleanup/status gate for failed demo pods/jobs ✅
- `runtime/openapi_demo.py` and `runtime/openapi_demo/fake_crm_openapi.yaml` — Safe public-demo OpenAPI-to-MCP generator; checked-in fake spec only, dry-run artifact/catalog generation, no network/secrets/real deploy ✅
- `scripts/demo-openapi-to-mcp.py` — CLI entrypoint to generate and invoke the fake OpenAPI-derived MCP from a fresh checkout ✅
- `k8s/*.yaml` — Public-test deployment manifests; sensitive runtime, portal, registry, deploy, core-agent, and cron configuration is injected with Kubernetes Secret refs only; dev-only Docker stdio is moved to `k8s/dev-local/` ✅
- `runtime/tests/test_k8s_public_manifests.py` — Static regression guard for plaintext secret placeholders and public-test Docker stdio/ephemeral-key settings ✅
- `runtime/start-all.sh` — Helper to start all services ✅
- `docker-compose.yml` — One-command local stack (db + keygen + migrate + registry + deploy + router + portal + demo-sandbox-mcp); generates persistent auth keys into `sealfleet_keys`, bootstraps the admin login via `ADMIN_INITIAL_PASSWORD`, and applies `restart: unless-stopped` so the stack survives host restarts ✅

**Key interfaces:**
- `POST /channels` — Register a channel with policy
- `POST /manifests` — Register an MCP manifest (`mcp.server.register`)
- `POST /external-agents` — Register a tenant-scoped JSON-RPC external agent as `agent:<name>.invoke` using a sealed bearer-auth handle; records tenant ownership and rejects cross-tenant name overwrite (`agent.register`)
- `GET /manifests`, `GET /manifests/{name}` — List/read system plus caller-owned manifests; tenant-owned external-agent manifests are hidden from other tenants
- `GET /registry/export`, `POST /registry/import` — Tenant-scoped redacted catalog backup/restore (`registry.export`, `registry.import`)
- `POST /demo/openapi-to-mcp` — Public-demo OpenAPI-to-MCP creation dry-run; registers generated fake CRM manifest/catalog receipt without network/secrets/real deploy
- `GET /enterprise/contract` — Shared enterprise SSO/auth/compliance contract (`enterprise-auth-contract/v1`)
- `GET /audit/events` — Read audit events (`audit.read`)
- `POST /scim/v2/Users`, `PATCH /scim/v2/Users/{id}`, `PUT /scim/v2/Groups/{id}` — SCIM lifecycle and group-role mapping (`policy.admin`)
- `POST /publish/{channel}` — Publish message (with X-MCP-Name auth)
- `GET /subscribe/{channel}` — Read latest message (with X-MCP-Name auth)
- `GET /subscribe/{channel}/stream` — SSE real-time stream
- `POST /pipeline` — Run a multi-step tool pipeline through the shared hook/transport boundary
- `POST /call` — Direct MCP tool proxy through the shared hook/transport boundary
- `runtime.router._execute_mcp_tool(...)` — Internal shared HTTP/Docker-stdio execution boundary; applies pre/post runtime hooks and emits hook audit events

---

## `packages/mcpfinder-auth/` — Shared Auth + Enterprise Contract Package
**Status:** 🚧 In progress
**Purpose:** Reusable auth middleware and shared enterprise identity/compliance primitives for Sealfleet and adapters for other resource types.
**Language:** Python
**Dependencies:** PyJWT, httpx, starlette

**Modules:**
- `mcpfinder_auth/middleware.py` — JWT/JWKS middleware for MCP servers ✅
- `mcpfinder_auth/jwks.py` — JWKS token validation helpers ✅
- `mcpfinder_auth/resource_metadata.py` — OAuth protected resource metadata helper ✅
- `mcpfinder_auth/enterprise.py` — Enterprise contract models for orgs, tenants, subjects, service identities, OIDC/SAML/SCIM descriptors, RBAC/ABAC decisions, audit events, sealed handles/sessions, marketplace hooks, and Sealfleet adapters 🚧
- `tests/test_enterprise_contract.py` — Contract shape/redaction/adapter tests ✅

**Key interfaces:**
- `enterprise_contract_v1()` — Return serializable `enterprise-auth-contract/v1`
- `SealfleetResourceAdapter.tool_call(...)` — Map tool execution to shared policy/audit resource envelope
- `PolicyDecisionEnvelope.allow/deny(...)` — Product-neutral policy result object
- `AuditEventV1.from_policy_decision(...)` — Redacted audit event envelope
- `SealedHandleDescriptor.redacted_dict()` — Metadata-only sealed secret read shape

---

## `mcps/weather_trip/` — Weather Trip Planner MCP (public example)
**Status:** ✅ Complete
**Purpose:** Real daily weather (past week + next ~10 days) for up to 8 cities via key-less APIs (Open-Meteo primary, met.no fallback) + deterministic trip ranking
**Language:** Python
**Port:** 8080 (compose service `weather-trip-mcp`)

**Key interfaces:**
- `POST /call` — `fetch_cities_weather`, `rank_cities`
- `GET /tools` — List available tools
- `GET /health` — Health check

---

## `scripts/` — Process Manager
**Status:** ✅ MVP complete
**Purpose:** Start, stop, and check status of all Sealfleet services

**Scripts:**
- `scripts/start-all.sh` — Starts all 7 services with health checks ✅
- `scripts/stop-all.sh` — Stops all running services by PID ✅
- `scripts/status.sh` — Checks health of all service ports ✅

---

*Update this file as components are built. Mark status changes and note dependencies.*

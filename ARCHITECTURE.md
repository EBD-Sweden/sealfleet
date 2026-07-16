# Architecture

## Component Map

```
┌─────────────────┐      ┌──────────────────────────────────────────────┐
│   Core Agent    │      │ Sealfleet CLI (`python -m runtime.cli`)        │
│  (not in repo)  │      │  Local agent/operator control surface          │
└────────┬────────┘      │  - invoke/pipeline/workflow/manifest → router  │
         │               │  - mcp deploy/list/get → deploy service        │
         │               │  - cluster create/down → k3d/kubectl + scripts │
         │               └───────┬───────────────────────┬────────────────┘
         │                       │ (cluster create/down)  │ POST /deploy (SSE),
         │                       │ shells to k3d/kubectl   │ GET /deployments → deploy svc :8030
         │ opaque handles        ▼                         ▼
         │ + trace IDs only   ┌──────────────┐      ┌─────────────────┐
         │                    │ k3d / kubectl│      │ Deploy Service  │  MCP build/deploy
         │                    │ (cluster     │      │ (:8030)         │  from git repos
         │                    │  lifecycle)  │      └─────────────────┘
         ▼                    └──────────────┘
┌─────────────────┐  POST /call, /pipelines, /v2/pipelines, /jobs, /manifests (router :8040)
│ Runtime Router  │◀─────────────────────────────────────────────────────┘
│  (:8040)        │  Secure execution boundary for MCP tools/pipelines
│                 │  - Shared enterprise identity contract
│                 │  - Auth/authz enforcement
│                 │  - RBAC/ABAC policy decisions
│                 │  - Sealed input/credential resolution
│                 │  - Tracing/audit (`audit-event/v1`)
│                 │  - Tenant-scoped registry backup/import
│                 │  - External-agent MCP tool gateway (sealed auth)
│                 │  - Synchronous pipelines + durable async jobs
└────────┬────────┘
         │
    ┌────┼────┐
    ▼    ▼    ▼
┌──────┐┌──────┐┌──────┐
│ REST ││ gRPC ││ MCP  │  Adapters to internal services
│ API  ││ svc  ││ svc  │
└──────┘└──────┘└──────┘
```

The CLI is a thin control surface that drives **two backends** plus local cluster tooling:

- **Deploy service (`:8030`, separate from the router):** `mcp deploy` streams `POST /deploy` (SSE), `mcp list`/`mcp get` read `GET /deployments[/{name}]`. URL resolves flag (`--deploy-url`) > `MCPFINDER_DEPLOY_URL` > persisted config > `http://localhost:8030`.
- **Runtime router (`:8040`):** `invoke` → `POST /call`; `pipeline` → `/pipelines` + `/v2/pipelines` (synchronous run); `workflow` → `POST /jobs` (durable async); `manifest`/`mcp register` → `/manifests[/typed]`; `registry` → `/registry/export|import`.
- **Cluster lifecycle:** `cluster create`/`cluster down` shell out to real tooling — `scripts/start-local.sh` for `--mode local`, and `k3d cluster create/delete` + `kubectl apply -k k8s/dev-local/` for `--mode k3d`. They fail honestly (`dependency_missing`/`backend_error`) when tooling is absent and require `--yes` (and `--force` for non-`mcpfinder`-scoped k3d names) to tear down.

**`workflow` is a CLI facade over pipelines + jobs — there is no separate `workflow` primitive in the runtime.** A pipeline is the definition plus synchronous execution (`pipeline run`); a workflow is the *same* definition executed as a tracked, cancelable, pollable async **job** (`workflow run` → `POST /jobs`, `workflow status` → `GET /jobs[/{id}]`, `workflow cancel` → `POST /jobs/{id}/cancel`). The CLI invents no server concept.

## Data Flow: Tool Call

1. Agent calls MCP tool via gateway
2. Gateway checks auth (broker for credentials)
3. Gateway enforces coarse policy (allow/deny/require-confirm)
4. Gateway injects sealed inputs (if needed)
5. Shared `_execute_mcp_tool` runs configured pre-call hooks, dispatches via HTTP or Docker stdio, then runs post-call hooks; the tenant-owned `external_agent` transport is an explicit separate gateway boundary with sealed auth resolution and external-agent audit events rather than runtime hook execution
6. Gateway emits tool-call plus redacted runtime-hook/external-agent audit events with the same trace ID
7. Gateway returns the guarded/redacted result to agent

## Enterprise identity/compliance contract

Sealfleet now has a concrete shared contract slice in `packages/mcpfinder-auth/src/mcpfinder_auth/enterprise.py`, exposed by the runtime router at `GET /enterprise/contract`.

This contract is the reusable foundation for Sealfleet and adapters for other resource types:

- Organization/tenant model: organization membership is separate from product access; product-specific tenants/workspaces hang under organizations.
- Principals: users, groups/teams, service identities, marketplace identities, and system actors share stable subject semantics.
- SSO/provisioning: local/bootstrap, OIDC, SAML, SCIM, API key, and service-account integration descriptors keep IdP secrets as sealed/external references only.
- Authorization: product adapters map concrete resources into one `PolicyDecisionEnvelope` with subject, action, resource, context, decision, reason, policy version, and trace ID.
- Audit: `AuditEventV1` carries org/tenant/actor/resource/decision/result/trace fields and is redacted by default. Runtime policy/sealed/token-exchange receipts now propagate trace IDs through high-risk deny/resolve paths; named pipelines and direct `POST /call` use the authenticated subject for audit `user_id`; deploy catalog registration writes redacted `deploy.register` receipts for both success and registration failure paths. See `docs/OBSERVABILITY.md` for the emission map, readiness endpoints, and OTEL collector guidance.
- Sealed secrets/sessions: `SealedHandleDescriptor` and `SessionDescriptor` define ownership, purpose, scope, expiry, single-use/revocation, and metadata-only read/list semantics.
- Marketplace hooks: marketplace buyer/entitlement identity feeds policy context but never replaces authorization.

The first Sealfleet adapter defines mappings for `mcp.tool.call`, `mcp.pipeline.run`, `credential.use`, `sealed_handle.resolve`, `deploy.create`, and `audit.read`. The runtime now enforces endpoint-level action gates for `audit.read`, `policy.admin`, `mcp.server.register`, `pipeline.invoke`, `agent.register`, `agent.invoke`, `credential.*`, and `sealed_handle.*` before sensitive route handlers run. External-agent named-pipeline stages carry the original request context into the external-agent dispatch boundary, and background `/jobs`/`/jobs/batch` pre-authorize `pipeline.invoke` plus `agent.invoke`, bind jobs to the authenticated tenant unless the caller has platform/global authority, and replay already-authorized subject/action context in the worker, including positive DB role/SCIM group grants, so `mcp.pipeline.run`/`pipeline.invoke` permission cannot substitute for `agent.invoke` before network egress. External-agent manifests are owned in the registry tenant map, catalog reads are filtered to system plus caller-owned manifests, and cross-tenant `agent:<name>` overwrite is rejected. Portal JWT claims are preserved on the runtime identity so permissions and SCIM/IdP groups participate in authorization; DB-backed API keys load `api_keys.action_permissions` and fail closed when that metadata is missing for protected enterprise actions. Durable `action_permissions` grants are intentionally limited to user and role grantees until API-key/service-account subject identity is implemented, preventing dead grant paths. Portal delegated identity headers (`X-Sealfleet-User-Id` / `X-Sealfleet-Tenant-Id`) are trusted only for API keys explicitly opted in by durable `api_keys.allow_identity_delegation` / metadata or by configured key SHA-256 fingerprint; display names are not privileged. The portal adds a default-deny `/api/*` edge policy with a short public allowlist plus shared proxy handler auth that forwards a scoped backend API key or bounded portal backend JWT. SCIM lifecycle endpoints sit behind `policy.admin`: user deactivation revokes SCIM-managed role grants and active sessions, PATCH treats `/scim/v2/Users/{user_id}` as authoritative and rejects body identifiers that resolve elsewhere, and SCIM group updates maintain external group-to-local-role mappings used by the JWT group authorization path. A design-only data-warehouse adapter maps SQL/warehouse/catalog/stage/integration actions into the same envelope without copying Sealfleet endpoint details.

## Sealed Input Flow

1. Agent identifies sensitive input needed
2. Agent requests `secure_input(fields=[...])`
3. Secure UI prompts user (modal/form)
4. UI creates a sealed handle bound to the authenticated tenant and subject (user id or API-key id) and receives only metadata
5. Gateway resolves the handle only inside pipeline execution, with tenant/subject ownership, expiry, and atomic single-use (`used_at IS NULL`) checks
6. Public HTTP plaintext handle resolution is disabled and audited as denied; create/delete/resolve attempts produce redacted audit events
7. LLM only sees: "Step completed" + metadata

## Key Decisions

*(Update this as you make architectural choices)*

- **Decision:** Python for MVP (fast prototyping)
- **Decision:** YAML for tool config (human-readable)
- **Decision:** OpenTelemetry for tracing (standard, vendor-neutral)
- **Decision:** Multi-product enterprise contract first, central control-plane service later. Keep contracts in `mcpfinder-auth` and product adapters in each product until semantics stabilize.
- **Decision:** Internal pipeline mode and public platform mode are separate deployment modes; internal operator shortcuts cannot be documented as public enterprise capabilities.
- **Decision:** Portal admin APIs split `platform_admin` from tenant-scoped `tenant_admin`/`admin`: platform CRUD/shared server metadata require platform admin; tenant admins are DB-verified server-side and tenant-filtered to their own users, roles, and SSO mappings. Tenant admin roles only authorize when `roles.tenant_id` matches the session tenant; `platform_admin` roles only authorize when platform-scoped (`roles.tenant_id IS NULL` or tenant slug `platform`). SSO user upsert rejects cross-tenant email collisions, is authoritative only for `assignment_source='sso'` grants, and cannot map `platform_admin`.
- **Decision:** Runtime portal session auth is RS256-first: the router validates portal JWTs through `JWKS_URL` or an explicitly configured portal public key cache, enforces issuer/audience when configured, and only permits legacy HS256 `NEXTAUTH_SECRET` migration tokens when `AUTH_ALLOW_LEGACY_PORTAL_HS256=true` in a non-production environment. New portal/backend proxy paths must use RS256/JWKS or explicitly scoped delegation API keys, not shared-secret user JWTs. Router-issued RS256 MCP access tokens use the separate router issuer and are not accepted as general portal sessions.
- **Decision:** Runtime enterprise RBAC is action-gated at endpoint boundaries. Portal JWT claim metadata is preserved for action/group checks, DB-backed API keys authorize only through durable `api_keys.action_permissions`, and DB-backed action grants currently authorize only user/role grantees; `api_key`/`service_account` grantee types are excluded from the migration contract until their subject identity model exists. Missing action metadata fails closed for protected endpoints. SCIM deactivation is responsible for session revocation plus SCIM-managed role cleanup, not just profile status updates. SCIM user upsert fail-closes on same-email rows owned by another tenant, SCIM PATCH rejects path/body identity mismatches before mutation, and structured runtime audit events persist `tenant_id` as a first-class column so `audit.read` queries are tenant-filtered unless the caller has explicit platform/global audit authority.
- **Decision:** Sealed-input storage is caller-owned and fail-closed: handles carry tenant/subject ownership, resolve only once through in-process pipeline execution, public HTTP resolve is denied to avoid plaintext responses, and production-like deployments require an injected KMS/Vault/k8s encryption key.
- **Decision:** External demo mode is a narrow sandbox, not a general public deployment: the executable v2 `demo_sandbox_invoice_review` pipeline is tenant/workspace-gated (`demo-sandbox` / `demo-external-evaluation`), quota/body-size checked in the runtime before execution, backed only by fake-data MCP tools, and paired with a Kubernetes egress-deny NetworkPolicy plus demo-scoped cleanup script.
- **Decision:** OpenAPI-to-MCP creation is exposed to public testers only as a fake-data dry-run differentiator path. `runtime/openapi_demo.py` accepts only the checked-in `runtime/openapi_demo/fake_crm_openapi.yaml` spec, rejects external URLs/raw secrets/non-demo tenant-workspace/privileged deploy actions in `public_demo`, generates a stdio wrapper plus typed manifest/catalog receipt, and can register that generated manifest in the runtime without network or Kubernetes side effects.
- **Decision:** gRPC reflection import starts as a disabled-by-default runtime creation spike in `runtime/grpc_reflection_importer.py`, not as an anonymous public discovery endpoint. Authenticated callers can convert protobuf reflection descriptors into typed Sealfleet manifests for unary RPCs only; client/server/bidi streaming methods are recorded as unsupported metadata and are not advertised as runnable tools. Live reflection rejects auth metadata unless the caller provides a preconfigured secure channel, and any local/dev insecure reflection must be explicitly enabled with no metadata. Reflection metadata auth values are redacted from generated manifests, and TLS/mTLS credential construction stays caller/runtime-owned rather than manifest- or LLM-visible.

- **Decision:** Runtime catalog backup/restore is tenant-scoped at the router boundary: `GET /registry/export` and `POST /registry/import` require explicit `registry.export`/`registry.import` permissions, bind bundles to the authenticated tenant, use the existing `McpManifest`/typed manifest/`NamedPipeline` models, redact sensitive fields recursively, and audit only counts/status summaries rather than raw bundle contents.
- **Decision:** Runtime plugin/policy hooks are an execution-boundary feature, not a marketplace: `runtime/policy_hooks.py` supplies deterministic pre-call/post-call hooks configured from runtime JSON/YAML, and `runtime/router.py::_execute_mcp_tool` applies them uniformly to direct tool calls, pipeline stages, named pipeline stages, HTTP transports, and Docker stdio transports. Built-in output length and secrets/PII guards can redact/truncate or fail closed; hook audit events include trace ID, hook name/action, result, tenant, subject, transport, and redacted reason.
- **Decision:** The Docker Compose quickstart is self-bootstrapping and restart-safe: a one-shot `keygen` service generates persistent RS256 signing keys, NextAuth secret, and encryption key into the `sealfleet_keys` volume (no ephemeral keys, so sessions and portal↔router JWKS trust survive restarts); `migrate` seeds the first admin login from `ADMIN_INITIAL_PASSWORD` only while `password_hash IS NULL`; the seeded local-dev API key carries `allow_identity_delegation` so portal-proxied calls are attributed to the real user/tenant in audit; per-tenant OIDC verifies ID tokens against the discovery document's `jwks_uri` (works with Keycloak/Okta/Azure AD, not just Auth0-style IdPs) and builds the IdP redirect URI from `NEXTAUTH_URL`; a live fake-data `demo-sandbox-mcp` runs under its k8s-style hostname alias so the catalog has one invocable tool out of the box.
- **Decision:** The Weather Trip Planner is the public reference for Sealfleet's core loop (build a pipeline → visualize its output): `mcps/weather_trip` fetches real daily weather from free key-less providers (Open-Meteo primary, met.no + Open-Meteo archive as fallback so a single provider outage doesn't kill the example) and ranks cities with deterministic arithmetic (no LLM); the `weather_trip_planner` v2 pipeline chains fetch → rank passing the whole step output between stages; the portal `/weather-trip` page runs it through the session-gated proxy and renders the ranking with dependency-free SVG/Tailwind visuals. Domain-specific dashboards belong in private overlays, not the public example surface.
- **Decision:** Public-test Kubernetes manifests are production-shaped and secret-ref only. Top-level `k8s/*.yaml` must not embed plaintext DB URLs, auth/session secrets, encryption keys, API keys, or dev placeholders; Docker stdio and ephemeral API-key minting are disabled there. Local Docker stdio remains available only through the explicit `k8s/dev-local/` overlay for disposable clusters.
- **Decision:** Audit hash-chain verification is fail-closed for canonical payload rows. Current audit writes tag `audit_hash_version=canonical-payload-v1`; `/audit/verify` only excuses unrecoverable multi-key JSONB order mismatches when a row is explicitly marked `legacy-json-payload-order` (or is an older untagged row that predates purpose/lawful-basis tagging), so tampered canonical rows cannot be masked as legacy.
- **Decision:** Sealfleet CLI is a project-specific MCP server Command Line Interface, not a cross-product CLI. The canonical entrypoint is `python -m runtime.cli` (`runtime/cli.py`), with `scripts/mcpfinder_cli.py` and `scripts/mcpfinder-cli` kept as thin compatibility wrappers. It validates `mcpfinder.cli.config/v1`, rejects other products scope bleed, maps `status` to `GET /health` + `GET /ready`, maps `invoke` to `POST /call`, maps manifest/registry commands to real runtime control-plane endpoints, exposes `smoke local-demo` for health/readiness/manifest/demo-tool smoke, redacts secret-looking values, and fails closed with structured `auth_missing`/`backend_unavailable`/validation errors instead of success-looking stubs when a backend or permission is missing.
- **Decision:** The CLI drives **two distinct backends** plus local cluster tooling rather than collapsing them into the router. MCP build/deploy (`mcp deploy`/`list`/`get`) targets the separate **deploy service** (default `:8030`, `--deploy-url`/`MCPFINDER_DEPLOY_URL`), while `invoke`/`pipeline`/`workflow`/`manifest`/`registry` target the **router** (`:8040`). `cluster create`/`connect`/`status`/`down` shell out to real tooling (`scripts/start-local.sh` for `--mode local`; `k3d` + `kubectl apply -k k8s/dev-local/` for `--mode k3d`), aggregate router + deploy health, and fail honestly with non-zero exit when tooling/backends are absent. Every backend-hitting command supports `--json` and `--dry-run`; URL resolution order is flag > env > persisted config (`~/.config/mcpfinder/cli.config.json`, written only by `cluster connect --save`) > default. New optional config keys are `deploy_url`/`kube_context`/`cluster_mode` and new scopes are `deploy`/`cluster`.
- **Decision:** `workflow` is a CLI facade over pipelines + jobs, not a separate runtime primitive. `pipeline run` is synchronous (`POST /v2/pipelines/run` or `/pipelines/{name}/run`); `workflow run` submits the same definition as a durable, cancelable, pollable async job (`POST /jobs`, `GET /jobs[/{id}]`, `POST /jobs/{id}/cancel`). `workflow create` is pure-local pipeline scaffolding and `workflow deploy` is identical to `pipeline deploy`; the CLI invents no server-side workflow concept.

## Dependencies Between Components

- `runtime` → `broker` (for credential injection)
- `runtime` → `policy` (for decision enforcement)
- `runtime` → `observability` (for tracing/audit)
- `portal` → `broker` (for sealed handle creation)
- `registry` → standalone (other components query it)

## Future: Federation Model

Organizations can run local registries and selectively federate with a central hub. Trust via signed registrations, TLS/mTLS, allowlists.

---

*Keep this updated as the system evolves. Note breaking changes, migration paths, and rationale for major decisions.*

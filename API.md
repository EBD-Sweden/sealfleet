# API Reference

## Runtime Router Enterprise Contract

### Public auth/discovery endpoints

The runtime router intentionally exposes only these unauthenticated endpoints in production mode:

- `GET /health` — liveness/status only.
- `GET /ready` — bounded readiness (`ready` or `degraded`) with dependency checks; public and secret-free.
- `GET /.well-known/jwks.json` — router public JWKS for verifying router-issued MCP access tokens.
- `GET /.well-known/oauth-protected-resource` — OAuth protected-resource metadata; advertises supported bearer method and MCP scopes.
- `GET /enterprise/contract` — schema/configuration metadata only; no secrets or tenant data.
- `POST /token` — RFC 8693 token exchange. This route is public at the HTTP middleware layer, but every request is validated with a portal subject token, IP + subject-token rate-limited, constrained to registered MCP resources, scope-checked (`mcp:call` for normal users; `mcp:admin` only for platform admins), and audited for both success and denial. Rate-limit storage prunes expired buckets on each check and is capped by `TOKEN_EXCHANGE_RATE_LIMIT_MAX_BUCKETS` (default `4096`) to avoid unbounded memory growth from rotating invalid subject tokens. Router-issued MCP access tokens are resource/audience-bound and are not accepted as general runtime/admin API bearer auth.

All other runtime endpoints require an API key or a portal session JWT. Portal session JWTs are validated as RS256 tokens using `JWKS_URL` (default `${PORTAL_URL}/api/.well-known/jwks.json`) or a configured `PORTAL_RS256_PUBLIC_KEY`/`PORTAL_JWT_PUBLIC_KEY` PEM cache. In production/public-test, `PORTAL_JWT_ISSUER`/`NEXTAUTH_ISSUER` and `PORTAL_JWT_AUDIENCE`/`NEXTAUTH_AUDIENCE` are required; whenever configured, issuer and audience are fail-closed; expired, malformed, wrong-key, wrong-issuer, missing-audience, and wrong-audience tokens are rejected. Legacy HS256 `NEXTAUTH_SECRET` support is disabled by default and only accepted when `AUTH_ALLOW_LEGACY_PORTAL_HS256=true` in a non-production deployment; it still uses the same configured issuer/audience checks as the RS256 path. Enterprise action gates now enforce explicit permissions for sensitive endpoints: `audit.read` (`GET /audit/events`), `policy.admin` (`/policy/*` and SCIM lifecycle), `mcp.server.register` (`POST /manifests*`), `registry.export` (`GET /registry/export`), `registry.import` (`POST /registry/import`), `sealed_handle.create`, `sealed_handle.resolve`, `credential.create`, `credential.read`, and `credential.use`. Portal JWT auth preserves `permissions`, `actions`, `scopes`, `scope`, `groups`, and `group_ids` claims on `request.state.identity`; API keys load durable `api_keys.action_permissions`, `api_keys.allow_identity_delegation`, and `api_keys.metadata` from the DB. `*` and `<prefix>.*` are supported. Missing API-key action metadata and explicit empty permission sets both fail closed for protected enterprise actions. `X-Sealfleet-User-Id` / `X-Sealfleet-Tenant-Id` delegated identity headers are ignored unless the authenticated API key is explicitly opted in by `allow_identity_delegation`, `metadata.allow_identity_delegation`, `metadata.portal_identity_delegation`, or an operator-configured `PORTAL_DELEGATION_API_KEY_SHA256S` fingerprint; key names are never trusted. Portal JWT group claims can resol... [truncated]

### External agent gateway parity slice

`POST /external-agents`

Registers a tenant-scoped external agent as an MCP-callable catalog tool. Required action: `agent.register`. Minimal supported protocol is JSON-RPC over HTTP(S) with bearer auth by sealed handle only.

Request shape:

```json
{
  "name": "qa-agent",
  "description": "Fake local agent for testing",
  "endpoint": "http://fake-agent.local",
  "protocol": "json_rpc",
  "auth": {"type": "bearer", "sealed_handle": "<sealed-handle-id>"},
  "timeout_ms": 5000
}
```

The router creates a tenant-owned manifest named `agent:<name>` with transport `external_agent` and tool `invoke`, so the resource appears only in that tenant's `GET /manifests`/`GET /manifests/{name}` views and can be called through `POST /call` or a runtime pipeline step. Cross-tenant re-registration of the same external-agent name is rejected to prevent manifest overwrite. Catalog/portal responses include only the sealed handle reference and never the raw bearer token.

`POST /call` with `{"mcp":"agent:qa-agent","tool":"invoke","inputs":{...}}` enforces tenant ownership, `agent.invoke` authorization, policy decision hooks, bounded in-process rate limiting, sealed auth resolution inside the runtime only, timeout clamping (`50ms..10s`), and redacted `external_agent.invoke` audit events. Named-pipeline execution passes the authenticated request context into external-agent dispatch. Background named-pipeline jobs (`POST /jobs` and `POST /jobs/batch`) require `pipeline.invoke`, pre-authorize `agent.invoke` before enqueueing any pipeline with an `external_agent` stage, bind persisted job tenants to `get_tenant_id(request)` unless the caller has platform/global authority, and replay the already-authorized subject/action context in the worker so explicit claims and DB/role/SCIM-granted authorizations survive the queue boundary while missing context fails closed before network egress. For direct MCP calls the route shares the same `_execute_mcp_tool` boundary as pipeline execution: HTTP manifests dispatch to the MCP `/call` endpoint, stdio manifests dispatch through `run_docker_stdio`, caller/request trace IDs propagate to policy, sealed-handle, hook, and `tool_call` audit events, the authenticated tenant/subject are recorded, and successful JSON-object responses include `trace_id`. Audit payloads record input keys/count and status metadata, not prompt bodies, auth values, or raw secrets.

Non-goals for this slice: no broad agent marketplace, no production Anthropic/OpenAI integration, no credential UX beyond sealed references, no non-JSON-RPC protocol support, and no durable external-agent persistence until the credential model is approved.

### Shared identity/compliance contract

`GET /enterprise/contract`

Returns the first implementation slice of the shared enterprise SSO/auth/compliance contract used by Sealfleet adapters and reusable by adapters for other resource types. The response is schema/configuration metadata only; it never contains IdP client secrets, signing keys, sealed input values, refresh tokens, or raw credentials.

Key response sections:
- `version`: currently `enterprise-auth-contract/v1`
- `boundary`: model-visible vs execution-boundary data contract (`opaque_handles`, `receipts`, `trace_ids`, and `redacted_metadata` only)
- `deployment_modes`: `local_dev`, `internal_pipeline`, `public_platform`
- `identity_core`: organizations, tenants/workspaces, users, groups/teams, role grants, service identities, and resources
- `auth_integrations`: local/bootstrap, OIDC, SAML, SCIM, API keys, and service accounts
- `policy_primitives`: RBAC/ABAC policy decision envelope fields
- `audit_event_schema`: append-only `audit-event/v1` envelope fields and redaction requirement
- `sealed_secret_session_model`: sealed handle/session descriptor fields and metadata-only read semantics
- `marketplace_identity_hooks`: AWS/GCP/Azure/license entitlement context that feeds policy but does not replace authorization
- `mcpfinder_adapter`: Sealfleet resources/actions including `mcp.tool.call`, `mcp.pipeline.run`, `credential.use`, `sealed_handle.resolve`, `deploy.create`, and `audit.read`
- `warehouse_adapter_design`: design-only mapping for SQL/warehouse/catalog/stage/integration resources

Example:

```bash
curl http://localhost:8040/enterprise/contract | jq '.version, .mcpfinder_adapter.actions'
```

### Python contract package

`packages/mcpfinder-auth` now exports reusable typed primitives from `mcpfinder_auth.enterprise`:

```python
from mcpfinder_auth.enterprise import (
    Organization,
    Tenant,
    EnterpriseSubject,
    ServiceIdentity,
    AuthIntegration,
    ScimProvisioningContract,
    PolicyResource,
    PolicyDecisionEnvelope,
    AuditEventV1,
    SealedHandleDescriptor,
    SessionDescriptor,
    MarketplaceIdentityHook,
    SealfleetResourceAdapter,
    enterprise_contract_v1,
)
```

`SealfleetResourceAdapter` defines how runtime actions map into the shared policy/audit envelope. The runtime router now applies endpoint-level action authorization for audit, policy, manifest registration, sealed-handle create/resolve/delete, credential CRUD/use, and SCIM lifecycle/group-role mapping. `/audit/events` is tenant-scoped by default: persisted structured audit events carry a first-class `tenant_id`, callers with `audit.read` only see their authenticated tenant, and cross-tenant audit reads require explicit platform/global audit authority. `/scim/v2/Users` supports create/upsert and deactivate (revoking sessions and SCIM-managed role grants); SCIM upsert rejects same-email collisions owned by another tenant instead of mutating the foreign row, and PATCH treats the path `{user_id}` as authoritative by rejecting body `id`, `userName`, or `email` values that point to another same-tenant or cross-tenant user before mutation. `/scim/v2/Groups/{group_id}` syncs external group-to-role mappings. Downstream transport execution paths must still carry the same policy/audit/secret boundary so HTTP, Docker stdio, future Kubernetes jobs, and marketplace connectors remain least-privilege end to end.

### Sealfleet CLI command/runtime interface

`runtime/cli.py` is the canonical Sealfleet-specific MCP server Command Line Interface for local agents and operators (`python -m runtime.cli`). `scripts/mcpfinder_cli.py` and `scripts/mcpfinder-cli` are compatibility wrappers only. It is not a generic EBD Sweden CLI and intentionally rejects other-product-scoped config.

The CLI spans two backends and is precise about which command hits which one: the **runtime router** (`runtime_url`, default `:8040`) for invoke/registry/manifest/pipeline/job operations, and the **separate deploy service** (`deploy_url`, default `:8030`) for building/deploying MCPs from git. The router does not own the deploy surface and vice versa. Every backend-touching command supports `--dry-run` (prints the exact request/target without network access); the global `--json` flag emits structured JSON for both success and error. Secret-looking fields are always redacted, and control-plane/cluster operations fail honestly with structured errors and a non-zero exit when their tooling or backend is unreachable.

Contract command:

```bash
runtime/.venv/bin/python -m runtime.cli --json contract
```

Config schema: `mcpfinder.cli.config/v1`

```json
{
  "schema": "mcpfinder.cli.config/v1",
  "product": "mcpfinder",
  "runtime_url": "http://localhost:8040",
  "allowed_scopes": ["runtime", "registry", "control-plane", "deploy", "cluster"],
  "deploy_url": "http://localhost:8030",
  "kube_context": "k3d-mcpfinder",
  "cluster_mode": "k3d"
}
```

- Required keys are unchanged: `schema`, `product` (must be `mcpfinder`), `runtime_url`, `allowed_scopes`.
- New **optional** keys: `deploy_url` (http(s) URL for the deploy service), `kube_context` (string), and `cluster_mode` (one of `local`, `k3d`, `remote`).
- `allowed_scopes` membership is validated against an additive allow-list that now includes `deploy` and `cluster` (alongside the existing `runtime`, `registry`, `control-plane`, `portal`, `mcps`, `docs`, `scripts`).
- New environment variables: `MCPFINDER_DEPLOY_URL` and `MCPFINDER_KUBE_CONTEXT` (alongside `MCPFINDER_RUNTIME_URL` and `MCPFINDER_API_KEY`).
- URL/context resolution order is **flag > env > persisted config > default**. The persisted config lives at `~/.config/mcpfinder/cli.config.json` (XDG-aware) and is written only by `cluster connect --save`; secret-looking keys are never persisted.

Core command/API mapping (runtime router, `runtime_url`):

- `validate --config <file>`: validates schema, `product=mcpfinder`, runtime URL, allowed scopes, and any optional `deploy_url`/`cluster_mode`/`kube_context`.
- `status`: calls `GET /health` and `GET /ready`.
- `invoke --mcp --tool [--payload | --payload-file] [--dry-run]`: builds or sends `POST /call {mcp, tool, inputs}`.
- `registry export/import`: calls `GET /registry/export` and `POST /registry/import?dry_run=<bool>` with an API key.
- `manifest list/get/register`: calls `GET /manifests`, `GET /manifests/{name}`, `POST /manifests`, or `POST /manifests/typed`.
- `smoke local-demo`: calls or dry-runs the local demo runtime checks: `GET /health`, `GET /ready`, `GET /manifests`, and `POST /call` for `demo-sandbox-mcp.get_demo_customer`.
- `smoke zero-to-hero [--deploy-url] [--runtime-url] [--dry-run]`: end-to-end public-preview smoke **across both backends** — deploy `GET /health|/ready|/deployments` and runtime `GET /health|/ready|/manifests|/pipelines|/v2/pipelines`. `--dry-run` prints the exact check list.

Cluster lifecycle (local host services or k3d; shells out to real tooling and fails honestly if `k3d`/`kubectl`/`docker` is absent):

- `cluster create --mode {local,k3d} [--name] [--bg] [--dry-run]`: local runs `scripts/start-local.sh`; k3d runs `k3d cluster create <name>` then `kubectl apply -k k8s/dev-local/`.
- `cluster connect --mode {local,k3d,remote} [--runtime-url] [--deploy-url] [--kube-context] [--save] [--dry-run]`: verifies `GET /health` on router (`:8040`) and deploy (`:8030`); `--save` persists resolved URLs/context/mode to `~/.config/mcpfinder/cli.config.json`. (Default `--mode remote`.)
- `cluster status --mode {...} [--dry-run]`: aggregates router + deploy `health`/`ready`; in k3d mode also runs `kubectl ... get deploy -l part-of=mcpfinder`. (Default `--mode remote`.)
- `cluster down --mode {local,k3d} [--name] --yes [--force] [--dry-run]`: destructive — requires `--yes`; local runs `scripts/start-local.sh --stop`, k3d runs `k3d cluster delete <name>` and refuses non-mcpfinder-scoped cluster names without `--force`. (Default `--mode local`.)

MCP lifecycle (deploy service at `deploy_url`, **not** the router — except `mcp register`):

- `mcp deploy --repo-url --name [--branch] [--description] [--tag ...] [--port] [--public/--no-public] [--env KEY=VALUE ...] [--dry-run]`: `POST {deploy_url}/deploy` and consumes the `text/event-stream`; live runs require `--api-key`/`MCPFINDER_API_KEY`.
- `mcp list`: `GET {deploy_url}/deployments`.
- `mcp get NAME`: `GET {deploy_url}/deployments/{name}`.
- `mcp register --file [--typed] [--dry-run]`: registers an already-running manifest **in the router** via `POST {runtime_url}/manifests` (or `/manifests/typed`); this is the one `mcp` subcommand that targets the runtime, not the deploy service.

Pipelines (router `runtime_url`; v2 templated YAML is the default engine, v1 named pipelines via `--engine v1`):

- `pipeline list [--engine {v1,v2,all}]`: `GET /pipelines` and/or `GET /v2/pipelines`.
- `pipeline get NAME [--engine {v1,v2}] [--type-check]`: `GET /pipelines/{name}` or `GET /v2/pipelines/{name}`; `--type-check` (v1 only) also fetches `GET /pipelines/{name}/type-check`.
- `pipeline deploy --file [--engine {v1,v2}] [--dry-run]`: v2 `POST /v2/pipelines/deploy {pipeline}`; v1 `POST /pipelines/register {pipeline}`.
- `pipeline run --name [--engine {v1,v2}] [--inputs JSON | --inputs-file] [--dry-run]`: **synchronous** — v2 `POST /v2/pipelines/run {pipeline, inputs}`; v1 `POST /pipelines/{name}/run {inputs}`.
- `pipeline reload`: `POST /pipelines/reload`.

Workflows (a CLI facade over pipelines + jobs; there is no separate workflow primitive in the runtime — see the decision note below):

- `workflow create --name [--engine {v1,v2}] [--step mcp.tool ...] [--output] [--dry-run]`: pure-local; scaffolds a pipeline definition file (**v1 default**, because `workflow run` → `POST /jobs` resolves only v1 named pipelines) at `runtime/pipelines[/v2]/{name}.yaml` unless `--output` is given. Use `--engine v2` only to scaffold a definition you will run synchronously via the `pipeline` group.
- `workflow deploy --file [--engine {v1,v2}] [--dry-run]`: same as `pipeline deploy` (**v1 default**, matching `workflow run`).
- `workflow run --name [--inputs | --inputs-file] [--job-name] [--dry-run]`: **async/durable** — `POST /jobs {pipeline, inputs, name}` returns a `job_id`. This is the key distinction from `pipeline run`.
- `workflow status [--job-id | --list] [--status] [--dry-run]`: `GET /jobs/{job_id}` or `GET /jobs?status=`.
- `workflow cancel --job-id [--dry-run]`: `POST /jobs/{job_id}/cancel`.

Workflow-vs-pipeline decision (stated honestly): a **pipeline** is the definition plus synchronous execution; a **workflow** is that same definition executed as a tracked, cancelable, pollable async **job**. The CLI invents no server concept — `workflow run` simply maps to `POST /jobs`.

Dry-run-first examples (safe to copy-paste; they print the exact request/target without touching a backend):

```bash
# Inspect what a v2 pipeline run would send, then run it for real with an API key.
python -m runtime.cli --json pipeline run --name demo --inputs '{"x":1}' --dry-run
MCPFINDER_API_KEY=... python -m runtime.cli --json pipeline run --name demo --inputs '{"x":1}'

# Deploy an MCP from git through the deploy service (:8030), dry-run first.
python -m runtime.cli --json mcp deploy --repo-url https://example/repo --name my-mcp --dry-run

# Submit the same definition as a durable async job, then poll it.
python -m runtime.cli --json workflow run --name demo --inputs '{"x":1}' --dry-run
```

Failure contract: missing auth returns `auth_missing`, invalid payload/config returns structured validation errors (e.g. `schema_invalid`, `config_invalid`, `project_scope_violation`), and unavailable runtime/deploy/control-plane backends return `backend_unavailable` (or `backend_error` for non-2xx HTTP) with a non-zero exit. Missing external tooling returns `dependency_missing`; `cluster down` without `--yes` returns `confirmation_required`; deleting a non-mcpfinder-scoped k3d cluster without `--force` returns `scope_violation`. The CLI redacts secret-looking fields in backend responses and tool output and does not print raw API keys.

### Runtime hook boundary

`runtime/policy_hooks.py` defines the transport-agnostic runtime hook API used by `runtime/router.py` before and after MCP execution:

```python
RuntimeHookContext(trace_id, tenant_id, subject_id, mcp, tool, transport, pipeline_name="")
HookManager.run_pre_call(ctx, inputs) -> inputs
HookManager.run_post_call(ctx, result) -> result
build_runtime_hook_manager({"runtime_hooks": {"enabled": true, "hooks": [...]}})
```

Hooks are configured by `MCPFINDER_RUNTIME_HOOKS_JSON` or `MCPFINDER_RUNTIME_HOOKS_FILE` (default `runtime/hooks.yaml`). Supported built-ins are `output_length_guard` (`mode: block|truncate`, `max_chars`) and `secrets_pii_guard` (`mode: redact|block`). In addition, `manifest_pii_guard` is ALWAYS active regardless of hook config: manifest-declared `pii_fields` (per tool or MCP-wide dot paths; list traversal; `*` wildcard) are redacted from every tool result at the boundary, audited by field name only, and YAML-seeded declarations cannot be dropped by self-registration. Hooks execute deterministically by `(order, name)`, blocking hooks fail closed with `403`/pipeline error, and each hook emits a redacted `runtime_hook` audit event carrying `trace_id`, hook name, hook action, result, tenant, subject, transport, and pipeline name. `_execute_mcp_tool(...)` is the shared execution boundary for `POST /pipeline`, named pipelines, direct `POST /call`, **v2 pipeline steps** (via `_call_mcp`), HTTP MCP transports, and Docker stdio transports.

**User MCP authorization (`_enforce_user_mcp_access`):** `/call`, `/pipeline`, `/pipelines/{name}/run`, and `/v2/pipelines/run` enforce per-MCP + per-tool grants for portal JWTs AND delegated API-key identities (previously delegated calls were unchecked). Resolution: `users.is_admin` bypass → direct `mcp_permissions` grant → role grant → IdP group claim grant via `scim_group_role_mappings`/`sso_role_mappings` (`idp_claim_key IN ('groups','roles')`). `mcp_permissions.allowed_tools` is enforced per call (NULL/empty = whole MCP). Manifest `access.allowed_roles`/`access.allowed_groups` adds a declarative gate evaluated first; pure service API keys (no delegated user) are exempt from both. Delegation-enabled callers may forward `X-Sealfleet-Groups` (comma-separated) alongside the user/tenant delegation headers. Audit events carry GDPR `purpose`/`lawful_basis` columns (defaults derived from the action; both included in the tamper-evident hash for new rows only, so pre-migration rows still verify), `GET /privacy/export` returns `schema_version: 2` with the subject's `audit_trail` (≤1000 rows + truncation flag), and a scheduled retention loop (`MCPFINDER_RETENTION_SCHEDULE`, default on, every `MCPFINDER_RETENTION_INTERVAL_HOURS`=24h) runs `prune_operational_data()` and emits a `retention.prune` audit event reporting audit rows past `AUDIT_RETENTION_DAYS`.

## Portal API Auth Boundary

The portal uses a default-deny edge policy in `portal/src/middleware.ts`/`portal/src/lib/portal-route-policy.ts`: only login, slash-boundaried NextAuth callback children, public JWKS/OAuth Protected Resource metadata, SSO start, and health/readiness are explicitly public. Exact public paths do not imply similarly named children/siblings (for example `/api/sso/start-admin` stays protected). Sensitive `/api/*` routes return `401` before proxying when no session is present.

Runtime/deploy/credentials/policy/sealed proxy handlers additionally call `requirePortalSession()` from `portal/src/lib/portal-auth.ts` so direct handler invocation is fail-closed too. Production proxy calls authenticate to the runtime with a scoped server-side API key (`RUNTIME_API_KEY`/`MCPFINDER_BACKEND_API_KEY`). The runtime trusts `X-Sealfleet-User-Id` / `X-Sealfleet-Tenant-Id` as delegated identity only when the authenticated API key is explicitly opted in through DB-backed `api_keys.allow_identity_delegation`, `api_keys.metadata.allow_identity_delegation`, `api_keys.metadata.portal_identity_delegation`, or an operator-configured `PORTAL_DELEGATION_API_KEY_SHA256S` fingerprint match. Without that trust gate, those user/tenant headers remain contextual request metadata and do not override the API key identity. Runtime endpoints that receive a caller-supplied Authorization bearer header may also validate a direct RS256 portal session JWT via configured JWKS/public key, issuer, and audience. The portal no longer mints or forwards a separate backend JWT for proxy calls. Sealed plaintext resolve is disabled at the portal layer and in the runtime HTTP route.

## External Demo Sandbox API

- `GET /api/health` and `GET /api/ready` (portal) are public, redacted, and `no-store` for external smoke checks.
- `POST /v2/pipelines/run` can execute `demo_sandbox_invoice_review` once `runtime/pipelines/v2/demo_sandbox_invoice_review.yaml` is loaded. Demo calls must authenticate as tenant `demo-sandbox` and provide `X-Workspace-ID: demo-external-evaluation` or a matching `workspace` input/body field.
- The demo pipeline fails closed on tenant mismatch, workspace mismatch, body size above 65 KiB, and more than 10 runs/hour per tenant/workspace/pipeline bucket. It calls only `demo-sandbox-mcp` tools and returns `fake-demo-only` output.
- `POST /demo/openapi-to-mcp` creates the public-demo OpenAPI-to-MCP differentiator artifact. Required body: `mode=public_demo`, `tenant_id=demo-sandbox` (or authenticated tenant), `workspace_id=demo-external-evaluation` (or `X-Workspace-ID`), `spec_ref=checked-in:fake-crm-openapi`, and `deploy_action=dry_run`. It rejects external spec URLs, non-demo tenant/workspace, raw `secrets`/`env_vars`/`credentials`, and privileged deploy actions unless an operator explicitly enables them outside the public path. On success it returns a traceable audit receipt, typed manifest, generated artifact paths, and catalog entry; no network fetch, real credential, or Kubernetes deploy occurs.
- CLI equivalent: `python scripts/demo-openapi-to-mcp.py --invoke`, which writes ignored local scratch artifacts under `runtime/.generated/demo-fake-crm-mcp/` and invokes the deterministic fake `get_demo_customer` tool locally without modifying the checked-in fixture at `runtime/generated/demo-fake-crm-mcp/`.
- The sample MCP manifest is `runtime/manifests/demo-sandbox-mcp.yaml`; Kubernetes demo isolation and egress-deny policy are in `k8s/demo-sandbox-mcp.yaml`.

## Portal Admin API

All `/api/admin/*` routes require an authenticated session and server-side DB role resolution in `portal/src/lib/admin-auth.ts`; the client-side `is_admin` flag is not sufficient authorization.

- `platform_admin`: required for platform-level tenant CRUD (`/api/admin/tenants`) and shared server metadata (`/api/admin/servers`). Granted by `PLATFORM_ADMIN_EMAILS` or an explicit manual `platform_admin` role that is platform-scoped (`roles.tenant_id IS NULL` or the role tenant slug is `platform`); tenant-scoped `platform_admin` role rows are ignored.
- `tenant_admin`: may list/create/update only users, roles, and SSO mappings whose `tenant_id` equals the caller's session tenant. Tenant-admin and legacy `admin` role names are ignored unless the role row's `tenant_id` matches the session tenant. Tenant admins cannot assign or SSO-map `platform_admin`.
- SSO role sync rejects an existing email row whose `tenant_id` differs from the tenant resolved for the SSO login, then deletes/recreates only `user_roles.assignment_source = 'sso'` grants on successful same-tenant login. Manual break-glass grants remain `assignment_source = 'manual'`.

## MCP Gateway

### Tool Registration

```python
def register_tool(
    name: str,
    description: str,
    schema: dict,
    handler: Callable
) -> None:
    """
    Register a tool with the MCP gateway.
    
    Args:
        name: Tool identifier (e.g., "crypto.price_quote")
        description: Human-readable description
        schema: JSON schema for tool parameters
        handler: Async function that executes the tool
    """
```

### Policy Enforcement

```python
def enforce_policy(
    user_id: str,
    action: str,
    resource: str,
    context: dict
) -> PolicyDecision:
    """
    Check if action is allowed.
    
    Returns:
        PolicyDecision(allow=True/False, reason=str, redactions=[])
    """
```

### Credential Injection

```python
def inject_credentials(
    tool_name: str,
    user_id: str
) -> dict:
    """
    Fetch credentials for tool execution.
    
    Returns:
        dict: Credentials (API keys, tokens, etc.)
    """
```

---

## gRPC Reflection Importer Spike

`runtime/grpc_reflection_importer.py` exposes an internal Python API for converting gRPC reflection descriptors into Sealfleet typed manifests. It is not mounted as an unauthenticated runtime endpoint.

```python
from google.protobuf import descriptor_pb2
from grpc_reflection_importer import GrpcReflectionImporter, ImporterOptions

descriptor_set = descriptor_pb2.FileDescriptorSet(...)  # from reflection or a local fixture
manifest = GrpcReflectionImporter(
    ImporterOptions(
        enabled=True,
        requester_identity="tenant-admin@example.com",
        metadata={"authorization": "Bearer ..."},
    )
).import_descriptor_set(
    descriptor_set,
    endpoint="grpc-service:50051",
    manifest_name="example-grpc",
)
```

Security and transport contract:
- Disabled by default: `ImporterOptions.enabled` must be true.
- Authenticated only: `requester_identity` is required before local descriptor import or live reflection discovery.
- Metadata values whose keys look like auth/token/secret/password/cookie are written as `<redacted>` in `manifest["x-grpc-reflection"]["metadata"]`.
- Live reflection never sends metadata over `grpc.insecure_channel`: `discover_from_reflection(...)` rejects metadata unless the caller passes a secure, preconfigured `secure_channel`.
- Local/dev insecure live reflection is explicit and metadata-free only: set `ImporterOptions(allow_insecure_reflection=True)` with an empty `metadata` mapping. The default rejects insecure live reflection before optional grpc imports/network use.
- TLS/mTLS credential construction is caller/runtime-owned and unsupported by the importer API; the importer stores no private keys, certs, or auth values in manifests/log/audit-facing output.
- Only unary RPCs become MCP tools. Streaming methods are listed in `x-grpc-reflection.unsupported_streaming`; Sealfleet makes no production claim for streaming execution in this spike.
- Live `discover_from_reflection(...)` requires optional `grpcio` and `grpcio-reflection`; local descriptor-set fixtures work with the base protobuf dependency.

---

## Registry API

### Tool Discovery

```python
def search_tools(
    query: str,
    filters: dict = None
) -> List[ToolMetadata]:
    """
    Search for available tools.
    
    Args:
        query: Search string
        filters: Optional filters (category, org, etc.)
    
    Returns:
        List of matching tools with metadata
    """
```

### Server Registration

```python
def register_server(
    server_id: str,
    endpoint: str,
    tools: List[dict],
    auth_methods: List[str]
) -> RegistrationResult:
    """
    Register an MCP server with the discovery service.
    """
```

### Tenant-scoped runtime catalog import/export

`GET /registry/export` requires `registry.export` and returns redacted runtime catalog metadata for the authenticated tenant:

```json
{
  "schema": "mcpfinder.registry.export",
  "schema_version": 1,
  "tenant_id": "tenant-a",
  "exported_at": "2026-05-29T00:00:00+00:00",
  "manifests": [],
  "typed_manifests": [],
  "pipelines": []
}
```

`POST /registry/import?dry_run=true|false` requires `registry.import`; the bundle `tenant_id` must match the authenticated tenant. The response is a per-item report with `validated`, `applied`, and `error` statuses so invalid items do not block valid siblings. Sensitive-looking keys are recursively redacted on export and raw bundle contents are not logged; audit events record only item counts/status summaries.

---

## Secure UI

### Sealed Input

`POST /sealed` creates a short-lived sealed handle for sensitive input and returns metadata only: `handle_id`, `label`, `created_at`, and `expires_at`. The router binds each handle to the authenticated `tenant_id` plus caller subject (`user_id` for portal JWTs, delegated portal user for explicitly privileged portal backend API keys, API key id for ordinary API-key callers), encrypts the value with `ENCRYPTION_KEY`/`MCPFINDER_ENCRYPTION_KEY` injected from KMS/Vault/k8s Secret, and emits a redacted `sealed_handle.create` audit event. Production-like deployments fail closed if no encryption key is configured; the deterministic development fallback is disabled for `production`/`prod`/`public-test` environments.

`GET /sealed` lists only active caller-owned handle metadata and never includes null-subject legacy handles. Public HTTP plaintext resolve (`GET /sealed/{handle_id}`) is disabled and audited as denied so secrets are not returned to browser/API clients. Pipeline execution resolves `{ "__handle": "..." }` only in-process and only when an authenticated tenant plus subject scope is present; the database operation is single-use, expiry-checked, and atomic (`used_at IS NULL` update with strict tenant/subject predicates). In-process resolve success and denial both emit redacted `sealed_handle.resolve` audit events that include handle id, tenant, subject, label/reason, and never plaintext. `DELETE /sealed/{handle_id}` requires the `sealed_handle.delete` action permission, invalidates only caller-owned unused handles, and emits redacted `sealed_handle.delete` audit events.

```javascript
// Create sealed input field
const sealedField = createSealedInput({
  fieldName: "api_key",
  type: "password",
  onSealed: (handle) => {
    // Send handle to agent instead of raw value
    agent.submitToolCall({ api_key: handle });
  }
});
```

---

## Broker

### Token Minting

```python
def mint_token(
    user_id: str,
    scope: str,
    ttl_seconds: int = 300
) -> str:
    """
    Create short-lived access token.
    
    Returns:
        JWT token
    """
```

### Signing (Crypto)

```python
def sign_transaction(
    user_id: str,
    chain: str,
    transaction: dict
) -> SignedTransaction:
    """
    Sign a crypto transaction using user's key.
    """
```

---

## Observability

### Tracing

```python
def create_trace(
    trace_id: str,
    operation: str,
    metadata: dict
) -> Span:
    """
    Create a trace span for an operation.
    """
```

### Audit Logging

```python
def log_audit_event(
    user_id: str,
    action: str,
    resource: str,
    result: str,
    trace_id: str
) -> None:
    """
    Record an immutable audit event.
    
    Schema:
        - who (user_id)
        - what (action)
        - where (resource)
        - when (timestamp)
        - why (context)
        - result (success/fail)
        - trace_id
    """
```

---

*Update this file as you implement APIs. Document parameters, return types, error conditions, and examples.*

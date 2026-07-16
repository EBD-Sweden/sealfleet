# Sealfleet Roadmap

## Registry Types (Platform Core)

### Type 1: Bring Your Own MCP ✅ (building now)
User has an existing MCP server running somewhere.
- Register endpoint + YAML in Sealfleet catalog
- Sealfleet handles discovery, docs, auth proxy, audit
- Example: mcp.example.com

### Type 2: Deploy from Git ✅ (building now)
User brings a Git repo with existing API code.
- Sealfleet builds Docker image + deploys to K8s
- Assigns managed endpoint: `api.sealfleet.io/<org>/<server>`
- Auto-registers in catalog
- K8s cluster: local k3d (mcpfinder), registry: localhost:5050

### Type 4: Secure Runtime ✅
Channel-based message routing between MCP tools with policy enforcement.
- Named channels (like Unix pipes): producers write, consumers read
- Runtime Router (port 8040) enforces channel policies, validates manifests, logs all messages
- Pipeline orchestration: chain MCP tools via `POST /pipeline`
- DB audit trail for all publish/subscribe/pipeline actions
- Example pipeline: weather-trip-mcp `fetch_cities_weather` → `rank_cities` (v2 pipeline `weather_trip_planner`, visualized in the portal)
- LLM never holds API credentials or knows where tools physically live
- **Type registry** (`runtime/types.yaml`) — primitives (String, Float, Integer) + domain types (WeatherData, OutfitRecommendation, StockPrice, VolatilityMetrics)
- **Type graph with auto-resolution** — directed graph traces producers/consumers, resolves chains automatically from desired output type
- **`POST /resolve`** — the LLM-facing endpoint: give it an output type + raw inputs, it resolves and executes the full tool chain
- **`GET /capabilities`** — LLM discovery: returns all producible types with their resolved chains and required inputs
- **`POST /manifests/typed`** — register typed manifests with input/output type declarations; validates against type registry on registration
- **Auto-generated PIPELINE.md docs** — each resolvable output type gets `runtime/pipelines/<Type>/PIPELINE.md` (human-readable) + `pipeline.yaml` (machine-readable), regenerated on startup
- **Broken chains detected at manifest registration time**, not mid-execution

### Type 3: Auto-Wrap from API Schemas 🚧 (public-demo OpenAPI slice live; gRPC spike)
User provides OpenAPI spec, Git repo with REST API, or an authenticated gRPC reflection source.
- Public-demo slice: checked-in fake CRM OpenAPI spec → typed MCP tool schema → fake stdio wrapper/manifest → dry-run catalog receipt → deterministic local invocation via `python scripts/demo-openapi-to-mcp.py --invoke`
- Public-demo safety: tenant/workspace locked to `demo-sandbox` / `demo-external-evaluation`; no URL spec fetch, raw secrets, network calls, or privileged deploy actions
- gRPC reflection spike: `runtime/grpc_reflection_importer.py` converts local/live protobuf descriptors into unary-only typed manifests when explicitly enabled by an authenticated caller; auth metadata is redacted and streaming RPCs are listed as unsupported
- External-agent gateway parity slice: tenant admins can register JSON-RPC HTTP agents as `agent:<name>.invoke` catalog tools with sealed bearer-auth handles, tenant/RBAC/policy/rate-limit/audit hooks, tenant-filtered manifest visibility, cross-tenant name-collision protection, named-pipeline and background-job `agent.invoke` enforcement before network egress, authenticated-tenant binding for `/jobs` and `/jobs/batch`, and redacted invocation logs; broad marketplace and production LLM-provider agent integrations remain non-goals until credential-model approval
- Future production path: packages + deploys to K8s with real auth, sealed credentials, policy gates, and operator approval
- Pipeline: API schema/reflection → tool schema → MCP wrapper/manifest → Docker build → K8s deploy → catalog

---

## Infrastructure (Local Dev)

- K8s: k3d cluster `mcpfinder` (k3s v1.31.5)
- Nodes: k3d-mcpfinder-server-0, k3d-mcpfinder-agent-0
- Local registry: localhost:5050 (k3d-mcpfinder-registry)
- Port mapping: 30080 → host

## Agent Layer ✅

- **Core Agent** (port 8050) — LLM-powered agent that discovers runtime capabilities, maps natural language questions to output_type+inputs via LLM, calls /resolve, and formats answers
- **Deploy → Runtime integration** — deploy service auto-registers typed manifests in the runtime router after successful k8s deployment
- **Process manager** — `scripts/start-all.sh`, `stop-all.sh`, `status.sh` for managing all 7 services
- **MCP server CLI** — `runtime/cli.py` (`python -m runtime.cli`) provides project-scoped config validation, health/readiness status, `/call` invocation, manifest/registry control-plane commands, local-demo smoke dry-run/live checks, JSON output, secret redaction, and non-zero structured failures; compatibility wrappers remain under `scripts/`.
- **Portal /ask page** — Chat-style interface to the core agent with chain badges, collapsible raw results, and response timing

## Current Sprint

Current focus: production hardening across security boundaries, observability, and deployment.

Enterprise identity/compliance layer for managed/hosted deployments: covers SSO/OIDC/SAML, org/team model, RBAC/ABAC, audit, SCIM, sealed secrets, and marketplace identity. First implementation slice is in `packages/mcpfinder-auth/src/mcpfinder_auth/enterprise.py` plus runtime `GET /enterprise/contract`; runtime now also has endpoint-level action gates and SCIM user/group lifecycle routes.

### P0 launch-hardening tasks
- [x] Shared enterprise identity/compliance contract and Sealfleet adapter/API slice
- [x] Runtime enterprise RBAC gates for audit/policy/manifest/credential/sealed endpoints plus SCIM lifecycle/group-role mapping regression coverage
- [x] Public demo/sandbox implementation
- [ ] Runtime security contract tests across `/call`, `/pipeline`, named pipeline runs, jobs, credentials, and stdio transport
- [x] Credential and sealed-handle redaction tests
- [ ] Portal API default-deny route wrappers/scanner before external testers
- [x] Deterministic demo tenant seed, public-demo policy pack, and sample MCP/pipeline fixtures
- [x] Public-demo OpenAPI-to-MCP creation dry-run with fake checked-in spec, catalog receipt, deterministic invocation, and negative safety tests
- [x] Public-test Kubernetes manifests consume sensitive values through Secret refs only; dev-local Docker stdio moved behind an explicit overlay and covered by static manifest tests
- [ ] Credentials-login/SSO rate limiting + failed-login audit events
- [ ] Shared transport adapter boundary for HTTP + Docker stdio with policy/audit wrapper
- [ ] Normalize ARCHITECTURE.md, API.md, and COMPONENTS.md against current auth/runtime/portal implementation

### Existing product sprint
- [ ] YAML + register an example MCP (Type 1)
- [ ] Second MCP deployed to k3d (Type 2 proof of concept)
- [ ] Portal /test page: token input, tool picker, live MCP calls
- [x] K8s deploy pipeline in portal (connect Git repo → build → deploy → register) ✅
- [x] Core agent with LLM-powered Q&A (port 8050) ✅
- [x] Deploy → runtime auto-registration ✅
- [x] Process manager scripts (start-all, stop-all, status) ✅
- [x] Portal /ask chat page ✅

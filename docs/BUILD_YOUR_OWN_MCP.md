# Build your own MCP

Sealfleet is a platform for exposing your own capabilities as agent-callable MCP
tools, with secure execution, policy enforcement, and full observability. This
guide walks through authoring an MCP, describing it with a manifest, deploying
it, and invoking it through the runtime router and CLI.

The worked example is `mcps/demo_sandbox/` — a small, dependency-light MCP that
exposes two fake tools. Copy it as a starting point.

## 1. Anatomy of an MCP

An MCP is just an HTTP service that exposes three endpoints:

| Method & path | Purpose |
|---|---|
| `GET /health` | Liveness/readiness — returns `{"status": "ok"}` |
| `GET /tools`  | Lists the tools this MCP provides (name + description) |
| `POST /call`  | Executes a tool: body `{"tool": "<name>", "inputs": {...}}` → result |

A minimal server (FastAPI) looks like:

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="My MCP")

class ToolCall(BaseModel):
    tool: str
    inputs: dict = {}

def greet(name: str) -> dict:
    return {"message": f"Hello, {name}!"}

TOOLS = {"greet": greet}

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}

@app.get("/tools")
def tools() -> list[dict]:
    return [{"name": "greet", "description": "Greet a person by name."}]

@app.post("/call")
def call_tool(call: ToolCall) -> dict:
    fn = TOOLS.get(call.tool)
    if not fn:
        raise HTTPException(400, f"unknown tool: {call.tool}")
    return fn(**call.inputs)
```

That is the whole contract. Your tools can call any API, database, or service
you control — the platform never sees your credentials (see the Architecture
Principles in `ARCHITECTURE.md`: the LLM is a planner, not a secret-holder).

## 2. Describe it with a manifest

The runtime router learns about your tools from a manifest in
`runtime/manifests/<your-mcp>.yaml`. It declares the endpoint, transport, and a
typed schema for each tool's inputs/outputs. Minimal example:

```yaml
id: my-mcp
name: my-mcp
endpoint: http://my-mcp.default.svc.cluster.local:8080   # in-cluster service DNS
transport: http
publishes: []
subscribes: []
tools:
  - name: greet
    description: Greet a person by name.
    inputs:
      name: {type: String, required: true, description: Person to greet}
    outputs:
      message: {type: String}
```

For an in-cluster MCP, `endpoint` must be the Kubernetes service DNS name, not
`localhost`/NodePort (see `docs/PIPELINE_ROUTING_GUARDRAILS.md` and
`docs/K8S_SERVICE_DISCOVERY.md`). See `runtime/manifests/demo-sandbox-mcp.yaml`
for a fuller manifest with metadata, quotas, and safety boundaries.

### Governance fields: PII redaction and role gating

Two optional manifest blocks let the operator govern a tool without touching
its code — the runtime enforces both:

```yaml
# Redact declared output fields at the execution boundary (GDPR data
# minimization). Dot paths into the result; lists are traversed element-wise;
# "*" matches any key. Per-tool or MCP-wide (top-level pii_fields:).
tools:
  - name: get_customer
    pii_fields:
      - customer.email
      - customer.ssn
      - orders.contact

# Gate the whole MCP to platform roles and/or raw IdP group claims.
# Applies to user-identity callers (portal sessions / delegated keys).
access:
  allowed_roles: [trading-ops]
  allowed_groups: [idp-traders]
```

Redaction is always on and audited by field name only. Self-registering MCPs
can add declarations but cannot drop what the YAML declares. Finer-grained
per-user/per-tool grants live in the `mcp_permissions` table (see
`AUTH_BACKEND.md` — `allowed_tools` is enforced per call).

## 3. Deploy it

Two paths, depending on where your MCP runs:

**A. Local / already-running HTTP service.** Point the manifest `endpoint` at
your running service and place the manifest in `runtime/manifests/`. The router
loads manifests on startup (and via reload).

**B. Git → Kubernetes via the deploy service.** Push your MCP (server +
`Dockerfile` + `requirements.txt`) to a git repo, then deploy it through the
deploy service, which builds the image, applies the k8s manifests, and registers
the typed manifest with the router:

```bash
# Dry run first — prints the plan without touching the cluster
python -m runtime.cli mcp deploy --repo-url https://github.com/you/my-mcp --name my-mcp --dry-run

# Real deploy (requires an operator key with mcp.server.register)
MCPFINDER_API_KEY=... python -m runtime.cli mcp deploy --repo-url https://github.com/you/my-mcp --name my-mcp
```

The deploy service streams progress and fails honestly if any stage errors —
it never reports a deploy as successful when it isn't.

## 4. Invoke it

Once registered, confirm the manifest and call a tool through the router:

```bash
# See your MCP and its tools
python -m runtime.cli manifest get my-mcp

# Invoke a tool (POST /call under the hood)
MCPFINDER_API_KEY=... python -m runtime.cli invoke \
  --mcp my-mcp --tool greet --payload '{"name": "Ada"}'
```

Every call produces a trace ID and audit event; secret-looking values are
redacted in CLI output.

## 5. Compose tools into pipelines

Single tools become workflows by chaining them into pipelines. A v2 templated
pipeline references your MCP's tools as steps:

```yaml
name: greet_and_log
version: 2
inputs:
  name: {type: string, required: true}
steps:
  - id: greet
    mcp: my-mcp
    tool: greet
    inputs: {name: "{{inputs.name}}"}
```

Deploy and run it:

```bash
python -m runtime.cli pipeline deploy --file greet_and_log.yaml --engine v2
python -m runtime.cli pipeline run --name greet_and_log --engine v2 --inputs '{"name":"Ada"}'
```

Run a pipeline as a durable, cancelable async job instead with the `workflow`
group (`workflow run`/`status`/`cancel`). See `docs/MCPFINDER_CLI.md` for the
full CLI surface and `docs/NEW_PIPELINE_TASKS.md` for pipeline authoring.

## 6. Security & observability you get for free

- **Least privilege** — every call is scoped by identity/action via
  `api_keys.action_permissions`; sensitive routes fail closed in production.
- **Sealed inputs** — collect secrets/sensitive data outside the LLM context via
  sealed handles; the model only sees opaque handles and redacted receipts.
- **Policy + audit** — the policy engine can deny/require-confirm tool calls, and
  every action emits an append-only audit event with a trace ID.

## 7. Private extensions (optional overlay)

The platform is extensible without forking. Each hook is a no-op until you
drop in an overlay file (all overlay paths are gitignored here, so you can
track them in your own private repo):

| Hook | Overlay path | What it does |
|---|---|---|
| Runtime endpoints | `runtime/router_internal.py` | Auto-loaded at startup when present; its `register(app)` may add routes, `MCP_DEPLOYMENT_MAP` entries, and `EXTRA_PIPELINE_LISTINGS` items |
| Runtime tests | `runtime/tests/test_router_internal_endpoints.py` | Tests for your overlay endpoints (skip themselves when the overlay is absent) |
| Portal nav | `portal/src/internal/nav-extra.ts` | Extra sidebar entries (resolved ahead of `src/internal-defaults/` via the `@internal/*` tsconfig alias) |
| Result renderers | `portal/src/internal/pipeline-renderers-extra.ts` | Register custom pipeline result renderers |
| Public routes | `PORTAL_EXTRA_PUBLIC_PATHS` env | Comma-separated extra exact public paths (e.g. an OAuth callback for a private page) |
| Channels | `runtime/channels_internal.yaml` | Additional channel declarations (documentation-only) |

Manifests, pipelines (`runtime/pipelines/`, `runtime/pipelines/v2/`), and
portal pages (`portal/src/app/<your-page>/`) are directory-based — extra files
work without any hook.

## Checklist

- [ ] MCP server exposes `GET /health`, `GET /tools`, `POST /call`
- [ ] Manifest in `runtime/manifests/<name>.yaml` with typed tool schemas
- [ ] `endpoint` uses in-cluster service DNS (not localhost/NodePort) for k8s MCPs
- [ ] Deployed (local manifest or git→k8s via the deploy service)
- [ ] `manifest get` shows it; `invoke` returns a real result
- [ ] (Optional) Composed into a pipeline/workflow

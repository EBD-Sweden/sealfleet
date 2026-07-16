# Pipeline Routing Guardrails

When creating or modifying an MCP pipeline that runs inside Kubernetes/k3d, never route MCP-to-MCP calls through `localhost`, `127.0.0.1`, `host.k3d.internal`, or NodePort URLs. Those are host-local paths and will break after pod/cluster restarts or when the caller runs in a different pod.

## Required pattern

For every in-cluster MCP backend:

- Kubernetes `Service.metadata.name` is the routable DNS name.
- Runtime manifest endpoint must be `http://<service-name>:<service-port>`.
- Router `ENDPOINT_OVERRIDES` must use the same service DNS route.
- Any k8s env var pointing from one app/MCP to another app/MCP must use service DNS.
- If the backend can scale to zero, add it to `MCP_DEPLOYMENT_MAP` in `runtime/router.py`.
- If a new service is referenced, add a Kubernetes `Service` manifest in `k8s/`.

Examples:

```yaml
# Good
endpoint: http://weather-trip-mcp:8080
endpoint: http://weather-trip-mcp:8080

# Bad for in-cluster MCP traffic
endpoint: http://localhost:8041
endpoint: http://127.0.0.1:8041
endpoint: http://host.k3d.internal:8041
endpoint: http://localhost:30080
```

## Allowed exceptions

Host-local URLs are allowed only for dependencies intentionally running on the host, not MCP services. Current test allowlist covers host-run LLM/search/public callback env vars such as:

- `LLM_BASE_URL`
- `OPENAI_BASE_URL`
- `SEARCH_PROXY_BASE_URL`
- `NEXTAUTH_URL`
- `AUTH_URL`

If a new exception is needed, document why it is host-only and add it explicitly to `HOST_ONLY_ENV_NAMES` in `runtime/tests/test_cluster_service_routing.py`.

## Mandatory check before merging a new pipeline

Run:

```bash
python3 -m pytest runtime/tests/test_cluster_service_routing.py -q
scripts/check-cluster-routing.sh
```

For live cluster confidence after restart-sensitive changes:

```bash
RUN_SMOKE=1 scripts/diagnose-cluster-restart.sh
```

The routing tests fail if a future pipeline introduces host/localhost MCP endpoints or forgets scale-to-zero coverage.

## Automation gates

These guardrails are enforced in multiple places:

- Local command: `scripts/check-cluster-routing.sh`
- CI workflow: `.github/workflows/cluster-routing-guard.yml`
- PR checklist: `.github/pull_request_template.md`
- New pipeline task template: `.github/ISSUE_TEMPLATE/new-mcp-pipeline.yml`
- Implementation checklist: `docs/NEW_PIPELINE_TASKS.md`

# New MCP Pipeline Implementation Tasks

Use this checklist whenever a user or agent asks to create a new MCP pipeline.

## 1. Define backend identity

- [ ] Choose the canonical MCP name: `<name>-mcp`.
- [ ] Choose the Kubernetes Service name. Default: same as MCP name.
- [ ] Choose the service port.
- [ ] Record the service DNS endpoint: `http://<service-name>:<port>`.

## 2. Add source-of-truth runtime assets

- [ ] Add `runtime/manifests/<name>-mcp.yaml`.
- [ ] Its `endpoint` must be `http://<service-name>:<port>`.
- [ ] Add tools with input schemas/descriptions.
- [ ] Do not use `localhost`, `127.0.0.1`, `host.k3d.internal`, or NodePort URLs for MCP-to-MCP calls.

## 3. Add Kubernetes assets

- [ ] Add/update a Kubernetes `Deployment`.
- [ ] Add/update a Kubernetes `Service` with `metadata.name: <service-name>`.
- [ ] Ensure `Service.spec.selector` matches the deployment pod labels.
- [ ] Add `/health` readiness/liveness probes when the backend is HTTP.
- [ ] Use `imagePullPolicy: IfNotPresent` for local k3d images unless a real registry push/pull is configured.

## 4. Wire router behavior

- [ ] If the backend can scale to zero, add it to `MCP_DEPLOYMENT_MAP` in `runtime/router.py`.
- [ ] If the router calls it directly in a named pipeline, call `await scale_manager.ensure_running("<name>-mcp")` before HTTP dispatch.
- [ ] Update `ENDPOINT_OVERRIDES` in `k8s/mcp-router.yaml` to the same service DNS endpoint.

## 5. Wire dependent services

- [ ] Any k8s env var that points to this MCP must use `http://<service-name>:<port>`.
- [ ] Only host-only dependencies may use host aliases; document exceptions in `runtime/tests/test_cluster_service_routing.py`.

## 6. Validate before handoff

Run:

```bash
scripts/check-cluster-routing.sh
python3 -m pytest runtime/tests/test_cluster_service_routing.py -q
```

For live/restart-sensitive changes, also run:

```bash
RUN_SMOKE=1 scripts/diagnose-cluster-restart.sh
```

A new pipeline is not done until these pass.

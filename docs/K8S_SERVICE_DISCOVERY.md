# Kubernetes service discovery contract for Sealfleet MCPs

This is the networking standard for any new MCP deployed into the Sealfleet cluster.

## Rule

Use Kubernetes **Service DNS names** for MCP-to-MCP traffic.

- Same namespace: `http://<service-name>:<port>`
- Cross namespace: `http://<service-name>.<namespace>.svc.cluster.local:<port>`
- Canonical fully-qualified form: `http://<service-name>.<namespace>.svc.cluster.local:<port>`

## Never use

- Pod IPs
- Kubernetes ClusterIP addresses directly
- Docker bridge IPs
- NodePorts for internal MCP-to-MCP traffic
- `localhost` as another service endpoint

## Exception

For **host-local dependencies only** such as local Postgres, Redis, LLM proxy, or BV viewer, use a stable host alias such as `host.k3d.internal`.

That exception does **not** apply to MCP-to-MCP traffic inside the cluster.

## Required manifest pattern

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: example-mcp
spec:
  selector:
    matchLabels:
      app: example-mcp
  template:
    metadata:
      labels:
        app: example-mcp
    spec:
      containers:
        - name: example-mcp
          image: mcpfinder-registry:5050/example-mcp:latest
          ports:
            - name: http
              containerPort: 8030
---
apiVersion: v1
kind: Service
metadata:
  name: example-mcp
spec:
  type: ClusterIP
  selector:
    app: example-mcp
  ports:
    - name: http
      port: 8030
      targetPort: http
```

## Deploy service behavior

`deploy/server.py` now generates:

- `Deployment` + `Service`
- `Service.type = ClusterIP`
- named container/service port `http`
- runtime/catalog endpoint registered as service DNS, not `localhost:NodePort`

Example registered endpoint:

```text
http://example-mcp.default.svc.cluster.local:8030
```

## Routing guard checklist

For every new or changed MCP pipeline, confirm:

- [ ] Runtime manifest endpoint uses Kubernetes Service DNS: `http://<service-name>:<port>`.
- [ ] No MCP-to-MCP endpoint uses `localhost`, `127.0.0.1`, `host.k3d.internal`, or NodePort URLs.
- [ ] Kubernetes `Service` exists for every referenced in-cluster MCP backend.
- [ ] Scale-to-zero backend is listed in `MCP_DEPLOYMENT_MAP`.
- [ ] Router `ENDPOINT_OVERRIDES` use the same service DNS endpoint.
- [ ] `scripts/check-cluster-routing.sh` passes locally.

Relevant docs:

- `docs/PIPELINE_ROUTING_GUARDRAILS.md`
- `docs/NEW_PIPELINE_TASKS.md`

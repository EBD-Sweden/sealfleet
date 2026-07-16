# Internal mTLS runbook (Linkerd) — SOC 2 CC6.7 (in-transit, service-to-service)

**Status:** ready-to-apply, **NOT auto-applied.** Installing a service mesh re-rolls
every workload and pulls control-plane + per-pod sidecar images. On this cluster
image pulls have been flaky (registry DNS) and the cluster was recently recovered
from an outage, so a mesh install must be done in a **deliberate maintenance window**,
not as a background change.

This closes the remaining CC6.7 gap: NetworkPolicies (`networkpolicy.yaml`) restrict
*who* may connect; edge/app TLS (`ingress.yaml`, `entrypoint.sh`) encrypt the edge;
**mTLS encrypts and mutually authenticates pod-to-pod traffic** (router↔MCP, router↔deploy/registry).

## Why Linkerd
- Automatic mTLS for all meshed TCP traffic with zero app changes (transparent proxy).
- Lighter than Istio; good fit for k3d/dev + small prod.
- Per-workload identity via short-lived certs (rotated automatically) — strong SOC 2 story.

## Prerequisites (do FIRST, to avoid the flaky-pull failure mode)
Pre-load the Linkerd images into every node's containerd so the install doesn't depend
on in-cluster DNS/registry (same pattern used during cluster recovery):

```bash
# Pin versions to your linkerd CLI: linkerd version --client
imgs=$(linkerd install --ignore-cluster 2>/dev/null | grep -oE 'image: [^ ]+' | awk '{print $2}' | tr -d '"' | sort -u)
for i in $imgs; do docker pull "$i" && k3d image import "$i" -c mcpfinder; done
# proxy + proxy-init images (from `linkerd inject`) must also be imported:
for i in $(linkerd inject --manual /dev/null 2>/dev/null | grep -oE 'image: [^ ]+' | awk '{print $2}' | tr -d '"' | sort -u); do
  docker pull "$i" && k3d image import "$i" -c mcpfinder; done
```

## Install (maintenance window)
```bash
linkerd check --pre
linkerd install --crds | kubectl --context k3d-mcpfinder apply -f -
linkerd install | kubectl --context k3d-mcpfinder apply -f -
linkerd check
```

## Mesh the mcpfinder workloads (rolling)
```bash
# Inject the data-plane sidecar into the default namespace (or per-deploy).
kubectl --context k3d-mcpfinder get deploy -o yaml \
  | linkerd inject - \
  | kubectl --context k3d-mcpfinder apply -f -
kubectl --context k3d-mcpfinder rollout status deploy --timeout=300s
```
Prefer namespace annotation for new workloads:
`kubectl annotate ns default linkerd.io/inject=enabled`.

## Verify mTLS (evidence)
```bash
linkerd viz install | kubectl apply -f -      # optional, for tap/edges
linkerd viz edges deployment -n default        # shows SECURED (mTLS) per edge
linkerd viz tap deploy/mcp-router -n default    # confirm tls=true on connections
```
Capture `linkerd viz edges` output (all edges `SECURED`) as the CC6.7 mTLS evidence artifact.

## Rollback
```bash
kubectl --context k3d-mcpfinder get deploy -o yaml | linkerd uninject - | kubectl apply -f -
linkerd uninstall | kubectl delete -f -
```

## Notes
- Keep the NetworkPolicies — mesh + netpol are complementary (encryption+authn vs reachability).
- For prod, drive the certs off a real trust anchor (cert-manager `trust-manager` or Vault) rather than the auto-bootstrapped Linkerd identity, and set a CA rotation schedule.
- Alternative if a mesh is undesirable: terminate TLS per-service via `entrypoint.sh` (`TLS_CERT_FILE`/`TLS_KEY_FILE`) and switch manifest `endpoint`s to `https://` — encrypts transport but does not provide mutual authentication.

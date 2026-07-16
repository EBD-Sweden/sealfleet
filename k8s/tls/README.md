# Sealfleet TLS / Encryption in Transit (SOC 2 CC6.7)

This directory contains the **encryption-in-transit** artifacts that move Sealfleet
off plain HTTP. Everything here is **additive** — the existing NodePort Services and
Deployments keep working unchanged; these add a TLS front door and opt-in HTTPS.

## Layers

| Layer | What | Status |
|---|---|---|
| **Edge TLS (primary)** | Traefik Ingress terminates HTTPS for router/deploy/registry/portal | `ingress.yaml` + a `mcpfinder-tls` Secret |
| **App-level TLS (defense in depth)** | Each FastAPI service can serve HTTPS itself when cert paths are mounted | `runtime/entrypoint.sh` + Dockerfile env, **opt-in** |
| **Internal segmentation** | NetworkPolicies restrict which pods may reach the control plane | `networkpolicy.yaml` |
| **Internal mTLS (NOT YET)** | Encrypted + mutually-authenticated service-to-service traffic | documented gap → Linkerd (below) |

## 1. Edge TLS via Traefik Ingress

### Dev (self-signed cert + manual Secret)

```bash
# 1. Generate a self-signed cert and render the TLS Secret manifest:
cd k8s/tls
APPLY=1 NAMESPACE=default ./gen-selfsigned-cert.sh
#   (omit APPLY=1 to just render k8s/tls/_certs/mcpfinder-tls.secret.yaml)

# 2. Apply the Ingress + HTTPS-redirect middleware:
kubectl --context k3d-mcpfinder apply -f ingress.yaml

# 3. Map hostnames to the node IP (k3d loadbalancer):
#    Add to /etc/hosts:  127.0.0.1 router.sealfleet.local deploy.sealfleet.local registry.sealfleet.local portal.sealfleet.local
#    (k3d publishes the Traefik LB on the host; adjust IP/port if needed)

# 4. Verify (self-signed -> -k):
curl -k https://router.sealfleet.local/health
```

> Note: Traefik's websecure entrypoint is :443 (NodePort 30303 on this cluster).
> If the Traefik controller pod is unhealthy (image pull issues on the dev
> cluster), the Ingress objects still validate but traffic won't flow until the
> controller recovers.

### Production (cert-manager + ACME / Let's Encrypt)

1. Install cert-manager (one-time):
   `kubectl apply -f https://github.com/cert-manager/cert-manager/releases/latest/download/cert-manager.yaml`
2. `kubectl apply -f cert-manager-prod.yaml` (creates the `letsencrypt-prod`
   ClusterIssuer + a `Certificate` that populates `mcpfinder-tls`).
3. On `ingress.yaml`, uncomment the
   `cert-manager.io/cluster-issuer: letsencrypt-prod` annotation and use real,
   publicly-resolvable DNS names. cert-manager will issue + auto-renew the cert.

`cert-manager-prod.yaml` also defines a `mcpfinder-selfsigned` ClusterIssuer for
internal clusters where cert-manager is installed but ACME is unreachable.

## 2. App-level TLS (defense in depth, opt-in)

`runtime/entrypoint.sh` (copied into each service image) starts uvicorn with
`--ssl-certfile/--ssl-keyfile` **only when** `TLS_CERT_FILE` and `TLS_KEY_FILE`
are set. With no cert env vars it serves plain HTTP exactly as before — the
default deployment is unchanged.

To enable per-pod HTTPS, mount the cert into the pod and set the env, e.g.:

```yaml
env:
  - name: TLS_CERT_FILE
    value: /tls/tls.crt
  - name: TLS_KEY_FILE
    value: /tls/tls.key
  # optional client-cert verification (toward in-cluster mTLS):
  # - name: TLS_CA_CERTS
  #   value: /tls/ca.crt
  # - name: TLS_VERIFY_CLIENT
  #   value: required
volumeMounts:
  - name: tls
    mountPath: /tls
    readOnly: true
volumes:
  - name: tls
    secret:
      secretName: mcpfinder-tls
```

When app-level TLS is on, update the probes to `scheme: HTTPS` and update peer
URLs (`RUNTIME_URL`, `REGISTRY_URL`, …) to `https://`.

## 3. Internal segmentation (NetworkPolicy)

`networkpolicy.yaml` applies default-deny ingress to the `part-of: mcpfinder`
pods, then allows only the intended callers per service. **Important:** k3d's
default flannel CNI does not enforce NetworkPolicy, so on the dev cluster these
are valid no-ops (cannot disrupt anything); on a policy-enforcing CNI
(Calico/Cilium in prod) they take effect — verify connectivity after applying.

## Remaining gap: internal mTLS

NetworkPolicies restrict *who* can connect but do **not encrypt or mutually
authenticate** service-to-service traffic. True internal mTLS is the remaining
CC6.7 work. Recommended next step: **Linkerd** (lightweight, automatic mTLS,
no app changes):

```bash
linkerd install --crds | kubectl apply -f -
linkerd install | kubectl apply -f -
kubectl annotate namespace default linkerd.io/inject=enabled
kubectl rollout restart deploy -n default     # re-inject sidecars
```

Linkerd transparently mTLS-encrypts all meshed TCP traffic. It was intentionally
**not** installed here to avoid destabilizing the recovered cluster and because
it requires pulling sidecar images (flaky in-cluster registry DNS). See
`docs/SOC2_GAP_ANALYSIS.md` CC6.7 for status.

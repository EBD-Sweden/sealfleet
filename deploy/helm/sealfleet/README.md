# Sealfleet Helm Chart

Deploys the full Sealfleet control plane to Kubernetes:

| Component | Port | Workload | Notes |
|-----------|------|----------|-------|
| registry  | 8010 | Deployment + ClusterIP Service | discovery |
| deploy    | 8030 | Deployment + ClusterIP Service | git→k8s MCP deploy service (single-writer) |
| router    | 8040 | Deployment + ClusterIP Service | runtime router; scale-to-zero RBAC |
| portal    | 3004 | Deployment + ClusterIP Service | web UI |

Plus:

- **Migrate Job** — one-shot Helm hook that clones the repo at a pinned ref and
  applies `db/migrations/*.sql`, `db/seeds/*` and `scripts/001_create_api_keys.sql`.
- **Ingress + TLS** — one host per service (Traefik by default; cert-manager optional).
- **Backup CronJob** — encrypted, rotated `pg_dump` (SOC2 A1.2).
- **NetworkPolicies** — default-deny + per-service allow (defense-in-depth).
- **Secret stub** — `DATABASE_URL`, `ENCRYPTION_KEY`, `ROUTER_RS256_PRIVATE_KEY`,
  `NEXTAUTH_SECRET` (replace before production, or BYO an existing Secret).
- **Postgres toggle** — external managed (default, recommended) or in-cluster (eval).

## Prerequisites

- Kubernetes 1.25+
- Helm 3.x
- An ingress controller (Traefik / nginx / ALB / GCE) if `ingress.enabled=true`
- (Prod) cert-manager for automated TLS, and a managed Postgres (RDS / Cloud SQL)
- Container images for the four services pushed to a reachable registry

## Quick start (evaluation, in-cluster Postgres)

```bash
helm upgrade --install mcpfinder deploy/helm/sealfleet \
  --namespace mcpfinder --create-namespace \
  --set postgresql.enabled=true \
  --set image.registry=ghcr.io/ebd-sweden/sealfleet \
  --set image.tag=v0.1.0
```

> The default Secret values are placeholders (`CHANGE_ME...`). For eval the stack
> will start, but you MUST replace them for any real use.

## Production install (external managed Postgres, BYO secrets)

1. Create a Secret out-of-band (sealed-secrets / External Secrets / SOPS / cloud
   Secrets Manager via CSI) with keys: `DATABASE_URL`, `ENCRYPTION_KEY`,
   `ROUTER_RS256_PRIVATE_KEY`, `NEXTAUTH_SECRET`.

   Generate values:
   ```bash
   # Fernet ENCRYPTION_KEY
   python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
   # RS256 private key (PEM)
   openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048
   # NEXTAUTH_SECRET
   openssl rand -base64 32
   ```

2. Install referencing that Secret:
   ```bash
   helm upgrade --install mcpfinder deploy/helm/sealfleet \
     --namespace mcpfinder --create-namespace \
     --set postgresql.enabled=false \
     --set secrets.create=false \
     --set secrets.existingSecret=mcpfinder-platform-secrets \
     --set image.registry=ghcr.io/ebd-sweden/sealfleet \
     --set image.tag=v0.1.0 \
     --set ingress.tls.certManager.enabled=true \
     --set ingress.tls.certManager.clusterIssuer=letsencrypt-prod \
     --set ingress.hosts.portal=portal.example.com \
     --set ingress.hosts.router=router.example.com \
     --set ingress.hosts.deploy=deploy.example.com \
     --set ingress.hosts.registry=registry.example.com
   ```

Prod-leaning defaults already set: `router.requireAuth=true`,
`router.allowEphemeralKeys=false`, `ingress.tls.enabled=true`.

## Key values

| Key | Default | Description |
|-----|---------|-------------|
| `image.registry` | `ghcr.io/ebd-sweden/sealfleet` | image repo prefix |
| `image.tag` | `latest` | tag for all services |
| `postgresql.enabled` | `false` | in-cluster eval Postgres vs external managed |
| `secrets.create` | `true` | create the Secret stub vs `secrets.existingSecret` |
| `migrate.enabled` | `true` | run the migrate/seed Job |
| `migrate.applySeeds` | `true` | apply `db/seeds/*` + api-key bootstrap |
| `migrate.git.repo` / `.ref` | repo / `main` | source of SQL assets (pin to image.tag) |
| `router.requireAuth` | `true` | `REQUIRE_AUTH` |
| `router.allowEphemeralKeys` | `false` | `AUTH_ALLOW_EPHEMERAL_KEYS` |
| `router.extraEnv` | `{}` | extra router env (LLM_*, ENDPOINT_OVERRIDES, K8S_SCALE_TO_ZERO) |
| `ingress.enabled` | `true` | TLS ingress, one host per service |
| `ingress.className` | `traefik` | override for nginx / alb / gce |
| `ingress.tls.certManager.enabled` | `false` | add cert-manager cluster-issuer annotation |
| `backup.enabled` | `true` | encrypted Postgres backup CronJob |
| `networkPolicy.enabled` | `true` | default-deny + per-service allow |
| `rbac.create` | `true` | router scale-to-zero RBAC |

See `values.yaml` for the full set (per-service replicas/resources, etc.).

## The migrate Job

Runs as a `post-install,post-upgrade` Helm hook. An init container clones
`migrate.git.repo@migrate.git.ref` to fetch the SQL (the chart deliberately does
not bake repo SQL into itself), then the main container waits for the DB and
applies migrations + seeds. Idempotent. **Pin `migrate.git.ref` to the same
release as `image.tag`.** If your cluster cannot reach the git repo, disable the
Job (`migrate.enabled=false`) and run migrations from CI / a bastion instead.

## Notes / reference-only

- **NetworkPolicies** are enforced only on a policy-aware CNI (Calico/Cilium);
  they are a no-op on flannel.
- **In-cluster Postgres** is a single-replica StatefulSet for evaluation only —
  no HA / PITR. Use managed Postgres in production.
- Ingress defaults target Traefik. For AWS ALB / GCE set `ingress.className` and
  `ingress.annotations` accordingly (the Terraform modules wire this up).

## Validate locally

```bash
helm lint deploy/helm/sealfleet
helm template mcpfinder deploy/helm/sealfleet            # default (external PG)
helm template mcpfinder deploy/helm/sealfleet --set postgresql.enabled=true
```

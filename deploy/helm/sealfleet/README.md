# Sealfleet Helm Chart

Deploys the full Sealfleet control plane to Kubernetes:

| Component | Port | Workload | Notes |
|-----------|------|----------|-------|
| registry  | 8010 | Deployment + ClusterIP Service | discovery |
| deploy    | 8030 | Deployment + ClusterIP Service | git→k8s MCP deploy service (single-writer) |
| router    | 8040 | Deployment + ClusterIP Service | runtime router; scale-to-zero RBAC |
| portal    | 3004 | Deployment + ClusterIP/NodePort Service | web UI |
| core-agent | 8050 | Deployment + ClusterIP Service | chat agent (pipelines + platform actions) |
| llm-proxy | 3456 | Deployment + ClusterIP Service | OpenAI-compatible LLM entry; `local/…` pinned to `llmProxy.local.baseUrl` |
| example MCPs | 8080 | Deployment + Service each (opt-in `exampleMcps.enabled`) | weather-trip, demo-sandbox |

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
- Container images for the services pushed to a reachable registry (or imported
  into the node's containerd — see "Local k3s" below)

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

## Local k3s (single box, on-cluster inference)

`values-k3s-local.yaml` installs the full stack — including the chat — on a
local k3s next to an OpenAI-compatible model server (e.g. vLLM, SGLang or
mistral.rs as an in-cluster Service). Images are imported into containerd
(`imagePullPolicy: Never`; on a multi-node cluster pin them with `nodeSelector`); the build/import commands
are at the top of that file. The migrate Job takes its SQL from the
`sealfleet-migrate-sql` image (`db/Dockerfile`) and seeds the operator key.

Create the Secret first (reusing the compose keys volume keeps sessions and
encrypted data compatible):

```bash
K=$(mktemp -d); docker run --rm -v mcpfinder_sealfleet_keys:/k:ro -v $K:/o alpine sh -c 'cp /k/* /o/ && chmod 644 /o/*'
PW=$(openssl rand -hex 16)
kubectl create ns sealfleet
kubectl -n sealfleet create secret generic sealfleet-platform-secrets \
  --from-literal=DATABASE_URL="postgresql://mcpfinder:$PW@sealfleet-postgresql:5432/mcpfinder?sslmode=disable" \
  --from-file=ENCRYPTION_KEY=$K/encryption-key --from-file=ROUTER_RS256_PRIVATE_KEY=$K/router-rs256.pem \
  --from-file=NEXTAUTH_SECRET=$K/nextauth-secret --from-file=RUNTIME_API_KEY=$K/runtime-api-key \
  --from-file=NEXTAUTH_RS256_PRIVATE_KEY=$K/portal-rs256.pem
rm -rf $K
helm upgrade --install sealfleet deploy/helm/sealfleet -n sealfleet \
  -f deploy/helm/sealfleet/values-k3s-local.yaml --set postgresql.auth.password="$PW"
```

The portal is then on `http://<node-ip>:30004`.

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

## The chat's shared key (`secrets.runtimeApiKey`)

The portal, chat agent and llm-proxy share one operator key. Set
`secrets.runtimeApiKey` (or put `RUNTIME_API_KEY` in your existing secret),
then register it so the router accepts it — with delegation, so chat calls are
attributed to the real user:

```sql
INSERT INTO api_keys (api_key, tenant_id, name, is_active, action_permissions, allow_identity_delegation)
VALUES ('<runtimeApiKey>', 'default', 'Chart portal/agent key', true,
        ARRAY['pipeline.invoke','agent.invoke','agent.register','mcp.server.register','registry.export','registry.import'], true)
ON CONFLICT (api_key) DO UPDATE SET is_active = true;
```

The key deliberately excludes `credential.*`; the chat resolves tenant LLM
credentials as the logged-in user. Non-admin users additionally need
`mcp_permissions` grants for the MCPs a pipeline touches (fail-closed RBAC).

## Runtime-authored pipelines (`router.persistence`)

Pipelines created at runtime — from the portal chat ("create a pipeline that…")
or `POST /v2/pipelines/deploy` — are written to a PVC mounted at
`router.persistence.mountPath` (`/data/pipelines`) and survive pod restarts and
image upgrades. The example pipelines shipped in the image are unaffected.

Pipelines are stored as files, so with `router.replicas > 1` every replica must
mount the same volume: set `router.persistence.accessMode: ReadWriteMany` on a
storage class that supports it (NFS, EFS, Filestore, …), or run one replica.
With a `ReadWriteOnce` claim and several replicas, a pipeline authored on one
replica is not visible to the others.

```yaml
router:
  replicas: 1
  persistence:
    enabled: true
    size: 256Mi
    accessMode: ReadWriteOnce
```

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

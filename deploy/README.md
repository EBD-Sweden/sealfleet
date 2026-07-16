# Deploying Sealfleet

This directory holds the managed-service / cloud-deploy story for Sealfleet:

```
deploy/
├── helm/sealfleet/        # Helm chart — full stack on any k8s cluster
└── terraform/
    ├── aws/               # EKS + RDS + Secrets Manager/KMS + ALB → installs the chart
    └── gcp/               # GKE + Cloud SQL + Secret Manager + GCE ingress → installs the chart
```

Other deploy assets (unchanged by this work):

- `../docker-compose.yml` — one-command local/dev stack.
- `../k8s/` — raw manifests used by the existing k3d dev cluster (the Helm chart
  mirrors these patterns: `k8s/tls/ingress.yaml`, `k8s/tls/networkpolicy.yaml`,
  `k8s/tls/cert-manager-prod.yaml`, `k8s/backup-cronjob.yaml`, `k8s/router-rbac.yaml`).

## Three ways to deploy

| Path | Use when |
|------|----------|
| `docker compose up` | local dev / quick eval (see repo root) |
| **Helm on an existing cluster** | you already run k8s and managed Postgres |
| **Terraform → EKS/GKE → Helm** | greenfield managed-service install on AWS/GCP |

---

## A. Helm on an existing cluster

Prereqs: a k8s cluster, `helm` 3.x, an ingress controller, and (recommended) a
managed Postgres + cert-manager.

```bash
# Evaluation (in-cluster Postgres, stub secrets — DO NOT use for real data):
helm upgrade --install mcpfinder deploy/helm/sealfleet \
  --namespace mcpfinder --create-namespace \
  --set postgresql.enabled=true \
  --set image.registry=ghcr.io/ebd-sweden/sealfleet \
  --set image.tag=v0.1.0
```

For production (external managed Postgres, BYO secrets, cert-manager TLS) follow
[`helm/sealfleet/README.md`](helm/sealfleet/README.md). Defaults are prod-leaning:
`REQUIRE_AUTH=true`, no ephemeral keys, TLS on.

The chart runs a one-shot **migrate Job** (Helm hook) that applies
`db/migrations/*.sql`, `db/seeds/*`, and `scripts/001_create_api_keys.sql`. It
fetches the SQL by cloning the repo at `migrate.git.ref` — pin that to the same
release as `image.tag`. If the cluster cannot reach the git repo, set
`migrate.enabled=false` and run migrations from CI/a bastion.

---

## B. Terraform → EKS/GKE → Helm

The Terraform modules stand up the cluster + managed Postgres + secret store and
then install the Helm chart in one `apply`.

```bash
# AWS
cd deploy/terraform/aws
terraform init && terraform apply
aws eks update-kubeconfig --name mcpfinder --region eu-north-1

# GCP
cd deploy/terraform/gcp
terraform init && terraform apply -var project_id=my-project
gcloud container clusters get-credentials mcpfinder --region europe-north1 --project my-project
```

See [`terraform/aws/README.md`](terraform/aws/README.md) and
[`terraform/gcp/README.md`](terraform/gcp/README.md) for inputs, outputs, pinned
module versions, and secret handling.

> The Terraform modules are **reference-grade**: `terraform fmt -check` and
> `terraform validate` pass (Terraform 1.12.2), but they have **not** been
> apply-tested against live cloud accounts. Review IAM scope, instance sizing,
> and module version pins before a production apply.

---

## Production hardening checklist

**Secrets**
- [ ] Replace every `CHANGE_ME` value; never ship stub secrets.
- [ ] Generate real `ENCRYPTION_KEY` (Fernet), `ROUTER_RS256_PRIVATE_KEY`
      (RSA-2048 PEM), `NEXTAUTH_SECRET` (`openssl rand -base64 32`).
- [ ] Manage secrets outside Helm values (External Secrets Operator / Secrets
      Store CSI / SOPS / sealed-secrets); set `secrets.create=false` +
      `secrets.existingSecret`. Avoid secrets in `--set`/state in plaintext.
- [ ] Restrict who can `kubectl get secret` and read Terraform state.

**Database**
- [ ] Use managed Postgres (`postgresql.enabled=false`); the in-cluster DB is
      eval-only (no HA/PITR).
- [ ] `sslmode=require` (or `verify-full`) in `DATABASE_URL`.
- [ ] Multi-AZ / regional HA, automated backups + PITR, deletion protection on.
- [ ] Verify the encrypted backup CronJob runs AND perform periodic restore
      tests (SOC2 A1.2 evidence); set `backup.s3Uri` for offsite copies.

**Router auth posture**
- [ ] `router.requireAuth=true` (default).
- [ ] `router.allowEphemeralKeys=false` (default) — fail closed without a real
      `ROUTER_RS256_PRIVATE_KEY`.
- [ ] Provision operator/tenant API keys via the portal/API, not the dev seed
      key (consider `migrate.applySeeds=false` in locked-down installs).

**Network / TLS**
- [ ] TLS on at the edge: cert-manager (`ingress.tls.certManager.enabled=true`)
      or cloud-managed certs (ACM on AWS, managed cert on GCP).
- [ ] Run on a NetworkPolicy-enforcing CNI (Calico/Cilium) so the bundled
      `networkPolicy` rules actually restrict traffic (no-op on flannel).
- [ ] Tighten egress and consider mesh/mTLS for internal hops.
- [ ] Lock down control-plane API access (private endpoints / authorized ranges).

**Images / supply chain**
- [ ] Pin `image.tag` to an immutable release (avoid `latest`).
- [ ] Pull from a private registry with `imagePullSecrets`; scan + sign images.
- [ ] Pin Terraform community module versions (already constrained; review bumps).

**Operations**
- [ ] Set realistic `replicas` (registry/router/portal ≥ 2) and resource
      requests/limits per environment.
- [ ] Wire observability (the platform emits trace IDs + audit events).
- [ ] Back up Terraform state in a remote, locked, encrypted backend.
```

# Deploying Sealfleet

Sealfleet runs anywhere Kubernetes does. Pick your path:

| Path | Use when | Jump to |
|---|---|---|
| **Docker Compose** | Local eval on one machine | [Local](#local-docker-compose) |
| **AWS (Terraform)** | Provision everything in *your* AWS account | [AWS](#aws--your-account-terraform) |
| **GCP (Terraform)** | Provision everything in *your* GCP project | [GCP](#gcp--your-project-terraform) |
| **Helm** | You already have a cluster + Postgres | [Helm](#helm-existing-cluster) |

All images are published to `ghcr.io/ebd-sweden/sealfleet-<service>` (runtime,
registry, deploy, portal, agent), signed with cosign and shipped with SBOMs.
Pin a released tag (e.g. `0.1.0`) or an immutable digest — never `latest` in
production.

---

## Local (Docker Compose)

```bash
git clone https://github.com/EBD-Sweden/sealfleet.git
cd mcpfinder
docker compose up --build      # Postgres + registry + deploy + router + portal
```

Migrations and seeds apply automatically; the portal comes up on
`http://localhost:3004`. This is for evaluation only (single host, no HA).

---

## Bring your own cloud (BYOF)

Both Terraform modules are **self-provisioning**: one `terraform apply` stands
up the network, a managed Kubernetes cluster, managed Postgres, KMS + a secrets
store, an ingress load balancer, and installs the Helm chart with generated
secrets. You run it in **your** account with **your** credentials; nothing
phones home.

### Prerequisites (both clouds)

- Terraform ≥ 1.5, `kubectl`, and the cloud CLI (`aws` or `gcloud`) authenticated.
- A domain you control for the portal/API hostnames + a TLS certificate
  (ACM on AWS, Google-managed cert on GCP).
- Your operator/CI egress IP(s) — the Kubernetes API is locked to an explicit
  CIDR allowlist (it will **refuse** `0.0.0.0/0`).

### AWS — your account (Terraform)

Provisions VPC + EKS + RDS Postgres (Multi-AZ) + KMS + Secrets Manager + the AWS
Load Balancer Controller, then installs the chart behind an ALB.

```bash
cd deploy/terraform/aws
terraform init
terraform apply \
  -var 'region=eu-north-1' \
  -var 'cluster_public_access_cidrs=["203.0.113.4/32"]' \   # your IP(s)
  -var 'acm_certificate_arn=arn:aws:acm:...:certificate/...' \
  -var 'ingress_hosts={portal="portal.example.com",router="router.example.com",deploy="",registry=""}' \
  -var 'image_tag=0.1.0'
```

- **Secret handling:** all platform secrets (DB URL, encryption key, RS256 key,
  NextAuth secret) are generated and stored KMS-encrypted in Secrets Manager;
  pass your own via the `encryption_key` / `router_rs256_private_key` /
  `nextauth_secret` variables to override.
- **Private API:** set `-var cluster_endpoint_public_access=false` for a fully
  private control plane (run the apply from inside the VPC or via a bastion).
- Validated: `terraform plan` against a live account produces a clean 75-resource
  create plan. Estimated run cost is non-trivial (EKS + Multi-AZ RDS) — size down
  (`db_instance_class`, `node_*`, `db_multi_az=false`) for eval.

### GCP — your project (Terraform)

Provisions VPC + private GKE (shielded nodes, Dataplane V2, Workload Identity) +
Cloud SQL Postgres (private IP, TLS-only) + Cloud NAT + Secret Manager, then
installs the chart behind a GCE ingress.

```bash
cd deploy/terraform/gcp
terraform init
terraform apply \
  -var 'project_id=my-project' \
  -var 'region=europe-north1' \
  -var 'master_authorized_cidrs=[{cidr="203.0.113.4/32",name="ci"}]' \
  -var 'managed_certificate_domains=["portal.example.com"]' \
  -var 'image_tag=0.1.0'
```

Enable the required APIs first (`container`, `sqladmin`, `servicenetworking`,
`secretmanager`, `compute`). Nodes are private and reach `ghcr.io` via Cloud NAT.

### Point DNS at the load balancer

After apply, read the outputs (`terraform output`) for the ALB/GCE address and
create DNS records for your `ingress_hosts`. TLS terminates at the load balancer
via your ACM / Google-managed certificate.

---

## Helm (existing cluster)

If you already run Kubernetes and manage Postgres yourself:

```bash
helm install mcpfinder deploy/helm/sealfleet \
  --namespace mcpfinder --create-namespace \
  --set image.tag=0.1.0 \
  --set secrets.databaseUrl='postgresql://user:pass@host:5432/mcpfinder?sslmode=require' \
  --set-file secrets.routerRs256PrivateKey=router-rs256.pem \
  --set secrets.encryptionKey="$(python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')" \
  --set secrets.nextauthSecret="$(openssl rand -base64 32)"
```

- **External secrets (recommended):** set `secrets.create=false` and
  `secrets.existingSecret=<name>` to consume a Secret you manage (External
  Secrets Operator, Sealed Secrets, SOPS, a cloud CSI driver). The chart then
  never renders secret material.
- **Eval Postgres:** `--set postgresql.enabled=true` runs an in-cluster
  StatefulSet (evaluation only; use managed Postgres in production).
- **Security posture:** every container runs non-root with dropped capabilities,
  no privilege escalation, and a RuntimeDefault seccomp profile. To also enforce
  `readOnlyRootFilesystem`, set `containerSecurityContext.readOnlyRootFilesystem=true`
  after mounting writable volumes for the router's pipeline directory.
- **Upgrade:** `helm upgrade mcpfinder deploy/helm/sealfleet --set image.tag=X.Y.Z`.
- **Teardown:** `helm uninstall mcpfinder` (Terraform: `terraform destroy` —
  clear `*_deletion_protection` first).

---

## Production readiness checklist

- [ ] Images pinned to a released tag or digest (not `latest`).
- [ ] Platform secrets generated/rotated and stored in a secrets manager, not in
      values files.
- [ ] Managed Postgres (RDS / Cloud SQL) with backups + deletion protection.
- [ ] Kubernetes API restricted to your CIDRs (or private).
- [ ] TLS certificate wired to the load balancer; HTTP redirects to HTTPS.
- [ ] `REQUIRE_AUTH=true`, ephemeral keys off (chart defaults).
- [ ] `migrate.applySeeds=false` (no dev API key in your DB — chart default).
- [ ] Per-tenant OIDC / SSO configured (see `AUTH_PORTAL.md`).
- [ ] Audit log retention + backup exports scheduled.
- [ ] Reviewed the accepted IaC findings in `deploy/.trivyignore`.

See also: `deploy/terraform/aws/README.md`, `deploy/terraform/gcp/README.md`,
`deploy/helm/sealfleet/README.md`, `AUTH_PORTAL.md`, and
`docs/compliance/` for the SOC 2 / GDPR control docs.

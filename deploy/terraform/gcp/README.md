# Sealfleet on GCP (Terraform reference module)

Provisions the full GCP footprint for Sealfleet and installs the Helm chart:

- **VPC** + subnet with secondary ranges (pods/services) and private-services
  access (VPC peering) for Cloud SQL private IP
- **GKE** cluster (Workload Identity, REGULAR release channel) + autoscaling node
  pool
- **Cloud SQL Postgres** (private IP, regional HA, PITR backups, deletion
  protection)
- **Secret Manager** secret holding the platform credentials
- Optional **Google-managed SSL certificate** for the GCE ingress
- **Helm release** of `deploy/helm/sealfleet` against the external Cloud SQL

> **Reference-grade.** fmt/validate-clean (Terraform 1.12, see below) but NOT
> apply-tested — no cloud credentials were available. Review IAM, sizing, and the
> private-services-access peering before any production apply. Uses native
> `google_*` resources (no community modules) to keep the surface inspectable.

## Usage

```hcl
module "mcpfinder_gcp" {
  source = "github.com/EBD-Sweden/sealfleet//deploy/terraform/gcp"

  project_id = "my-gcp-project"
  region     = "europe-north1"
  image_tag  = "v0.1.0"

  managed_certificate_domains = [
    "portal.example.com",
    "router.example.com",
    "deploy.example.com",
    "registry.example.com",
  ]
  ingress_hosts = {
    portal   = "portal.example.com"
    router   = "router.example.com"
    deploy   = "deploy.example.com"
    registry = "registry.example.com"
  }
}
```

```bash
terraform init
terraform plan
terraform apply
# then:
gcloud container clusters get-credentials mcpfinder --region europe-north1 --project my-gcp-project
```

Enable the required APIs first (container, sqladmin, servicenetworking,
secretmanager, compute).

## Inputs (selected)

See `variables.tf` for the full list with defaults.

| Variable | Default | Notes |
|----------|---------|-------|
| `project_id` | (required) | |
| `region` | `europe-north1` | |
| `kubernetes_version_prefix` | `1.30.` | GKE min master version |
| `node_machine_type` | `e2-standard-4` | |
| `db_tier` | `db-custom-2-7680` | Cloud SQL |
| `db_availability_type` | `REGIONAL` | HA |
| `managed_certificate_domains` | `[]` | set for managed TLS on GCE ingress |
| `image_registry` / `image_tag` | ghcr / `latest` | pin tag in prod |
| `install_helm_release` | `true` | set false to manage Helm separately |
| `encryption_key` / `router_rs256_private_key` / `nextauth_secret` | generated | provide to override |

## Outputs

`cluster_name`, `cluster_endpoint` (sensitive), `kubeconfig_command`,
`cloudsql_instance`, `cloudsql_private_ip`, `platform_secret_id`,
`ingress_hosts`, `database_url` (sensitive).

## Secrets handling

By default this module GENERATES the platform secrets, writes them to Secret
Manager, AND passes them to the chart's in-cluster Secret (`secrets.create=true`)
so a reference deploy is self-contained. For a stricter posture, install the
Secret Manager CSI driver, sync the `…-platform` secret into a k8s Secret, and
install the chart with `secrets.create=false --set secrets.existingSecret=…`.

## Validation performed

```
terraform fmt -check     # clean
terraform validate       # Success (Terraform 1.12.2, google provider resolved)
```

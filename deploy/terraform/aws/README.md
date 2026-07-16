# Sealfleet on AWS (Terraform reference module)

Provisions the full AWS footprint for Sealfleet and installs the Helm chart:

- **VPC** (public/private subnets, single NAT) — `terraform-aws-modules/vpc`
- **EKS** cluster + managed node group, IRSA enabled — `terraform-aws-modules/eks`
- **RDS Postgres** (encrypted with a dedicated KMS key, Multi-AZ, backups,
  deletion protection) — `terraform-aws-modules/rds`
- **Secrets Manager** secret (KMS-encrypted) holding the platform credentials
- **AWS Load Balancer Controller** (IRSA + Helm) for ALB ingress
- **Helm release** of `deploy/helm/sealfleet` against the external RDS

> **Reference-grade.** fmt/validate-clean (Terraform 1.12, see below) but NOT
> apply-tested — no cloud credentials were available. Review IAM scope, sizing,
> the LB-controller IRSA wiring, and pin the community module versions before any
> production apply.

## Pinned versions

This module pins community modules to majors whose variable/output contracts it
targets: `vpc ~> 5.0`, `eks ~> 20.0`, `rds ~> 6.0`, `iam ~> 5.39`, and the Helm
provider `~> 2.12` (block-style `kubernetes {}`). Bump deliberately and re-test;
newer majors rename arguments (e.g. EKS v21 `cluster_name`→`name`, Helm v3
`kubernetes = {}`).

## Usage

```hcl
module "mcpfinder_aws" {
  source = "github.com/EBD-Sweden/sealfleet//deploy/terraform/aws"

  region    = "eu-north-1"
  name      = "mcpfinder"
  image_tag = "v0.1.0"

  acm_certificate_arn = "arn:aws:acm:eu-north-1:123456789012:certificate/xxxx"
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
aws eks update-kubeconfig --name mcpfinder --region eu-north-1
```

## Inputs (selected)

See `variables.tf` for the full list with defaults.

| Variable | Default | Notes |
|----------|---------|-------|
| `region` | `eu-north-1` | |
| `kubernetes_version` | `1.30` | EKS control plane |
| `node_instance_types` | `["m6i.large"]` | |
| `db_instance_class` | `db.t3.medium` | RDS |
| `db_multi_az` | `true` | |
| `acm_certificate_arn` | `""` | ALB TLS cert (set for HTTPS) |
| `image_registry` / `image_tag` | ghcr / `latest` | pin tag in prod |
| `install_helm_release` | `true` | set false to manage Helm separately |
| `encryption_key` / `router_rs256_private_key` / `nextauth_secret` | generated | provide to override |

## Outputs

`cluster_name`, `cluster_endpoint`, `kubeconfig_command`, `rds_endpoint`,
`platform_secret_arn`, `ingress_hosts`, `database_url` (sensitive).

## Secrets handling

By default this module GENERATES the platform secrets (DB password, Fernet key,
RS256 key, NextAuth secret), writes them to Secrets Manager, AND passes them to
the chart's in-cluster Secret (`secrets.create=true`) so a reference deploy is
self-contained. For a stricter posture:

1. Set `install_helm_release=false`.
2. Install the External Secrets Operator or the Secrets Store CSI driver.
3. Sync the `…/platform` Secrets Manager secret into a k8s Secret.
4. Install the chart with `secrets.create=false --set secrets.existingSecret=…`.

## Validation performed

```
terraform fmt -check     # clean
terraform validate       # Success (Terraform 1.12.2, providers + modules resolved)
```

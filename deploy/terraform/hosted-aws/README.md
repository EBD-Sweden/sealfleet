# Hosted Sealfleet on AWS (App Runner) — OPTIONAL, badge-only

> **You probably do not need this.** The product model is: **GCP Cloud Run** runs
> the hosted cloud version (scale-to-zero, ~$0 idle), and **AWS = self-host** —
> customers deploy into *their own* AWS account via the BYOF terraform
> (`../aws`), so it costs *them*, not us. AWS Marketplace sells a **self-host
> license**; its fulfillment endpoint is served by the **GCP portal** (already
> ~$0 idle), so there is **no always-on AWS resource to run**.
>
> This module runs a *second always-on hosted instance on AWS*. Its only reason to
> exist is the **"Deployed on AWS"** marketing designation, and App Runner has **no
> scale-to-zero**, so it carries a ~$25–55/mo idle floor even with zero customers.
> Deploy it only if you specifically decide the badge is worth that floor.

Optional AWS-native deployment for the **"Deployed on AWS"** designation. It is
not required for the normal AWS Marketplace self-host license flow: Marketplace
fulfillment can be served by the existing GCP portal, and buyers then deploy into
their own account using the BYOF module. Use this module only if you intentionally
want a second hosted copy running on App Runner for badge/enterprise procurement
reasons.

## Architecture

```
buyer → App Runner (portal, HTTPS) ──► App Runner (router) ──► App Runner (registry)
                     │                        │                        │
                     └────────── Neon serverless Postgres (external) ──┘
        instance role: SecretsManager:GetSecretValue
                     + aws-marketplace:ResolveCustomer / GetEntitlements
        secrets in AWS Secrets Manager · images in ECR
```

- **App Runner** — managed HTTPS + autoscaling, no ALB/NAT/VPC to run. Three
  services (portal, router, registry). **No scale-to-zero** — `min_size` is `>=1`,
  so unlike Cloud Run this instance has a small always-on floor (see Cost).
- **ECR** — the `sealfleet-*` images are mirrored here (App Runner pulls from ECR,
  not ghcr.io).
- **Secrets Manager** — DATABASE_URL, generated platform secrets, license key,
  Stripe keys. App Runner injects them as `runtime_environment_secrets`.
- **IAM instance role** — grants Secrets Manager read **and**
  `aws-marketplace:ResolveCustomer` + `GetEntitlements`, so the portal's
  fulfillment handler needs **no access keys** on AWS (it uses the instance role).
- **Postgres** — external serverless (Neon). Use a **separate Neon project** from
  the GCP deployment: Marketplace buyers are tenants here.

## Cost (rough, us-east-1)

App Runner bills provisioned + active. With `min_size=1`, `cpu=512 (0.5 vCPU)`,
`memory=1024`:
- ~3 services × always-on floor ≈ **$25–55/mo** idle-ish, more under load.
- ECR + Secrets Manager: a few $/mo.
- Neon: free tier or ~$19/mo paid.

This is deliberately the **cheap** AWS path (no EKS/ALB/NAT). It costs more at idle
than Cloud Run (which scales to zero) — that's the price of the "Deployed on AWS"
badge. Keep GCP for direct traffic.

## Prerequisites

1. **AWS creds** for the account that will host (the Marketplace *seller* account,
   `ebd-partner` 338368293784, or a linked hosting account). Export a profile:
   `export AWS_PROFILE=ebd-partner`.
2. A **Neon project** for AWS tenants → pooled connection string (`sslmode=require`).
3. The **v0.7.0 images** available to mirror (built by CI at
   `ghcr.io/ebd-sweden/sealfleet-{runtime,registry,portal}:0.7.0`).

## Deploy

```bash
cd deploy/terraform/hosted-aws
cp terraform.tfvars.example ../../../../<somewhere-outside-repo>/aws.tfvars   # keep tfvars OUT of the repo
# edit: database_url, license_key, aws_marketplace_product_code, stripe_*, portal_domain

terraform init
terraform apply -var-file=/abs/path/aws.tfvars -target=aws_ecr_repository.svc   # create repos first
```

Then **mirror the images** into the new ECR repos (App Runner can't pull ghcr.io):

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGION=us-east-1
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com
for s in runtime registry portal; do
  docker pull ghcr.io/ebd-sweden/sealfleet-$s:0.7.0
  docker tag  ghcr.io/ebd-sweden/sealfleet-$s:0.7.0 $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/sealfleet-$s:0.7.0
  docker push $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/sealfleet-$s:0.7.0
done
```

Now the full apply:

```bash
terraform apply -var-file=/abs/path/aws.tfvars
```

Run DB migrations once against `database_url` (same `db/migrations/*.sql` as every
other deployment, including `016_aws_marketplace.sql`).

## After apply

`terraform output` gives you:
- `aws_marketplace_fulfillment_url` → set as the SaaS product's **Fulfillment URL**
  in the AWS Marketplace Management Portal.
- `aws_marketplace_notification_url` → subscribe the product's **SNS topic** here.
- `portal_custom_domain_dns` → the CNAME/validation records to add at your DNS
  provider for `portal_domain` (if set).

## Notes

- State: use a remote backend (S3+DynamoDB) for team use; add a `backend.tf`
  (gitignored) like the GCP module's GCS backend.
- `image_tag` bumps: push the new tag to ECR, change `image_tag`, `terraform apply`
  (App Runner redeploys; `auto_deployments_enabled=false` keeps deploys explicit).
- **No access keys on AWS.** The AWS SDK sources credentials from the App Runner
  instance role automatically. `awsMarketplaceConfigured()` gates on
  `AWS_ROLE_ARN` (or access keys) being set, so the module sets `AWS_ROLE_ARN` to
  the instance role ARN on the portal — that flips the check on without embedding
  any secret. If you deploy the portal somewhere without an instance role, fall
  back to `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`.

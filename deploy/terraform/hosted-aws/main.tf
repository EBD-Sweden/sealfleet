###############################################################################
# Hosted Sealfleet on AWS (App Runner) — the AWS-native deployment used for the
# AWS Marketplace channel so the listing earns the "Deployed on AWS" designation.
# Cost-efficient: App Runner (managed HTTPS + autoscaling) + ECR + Secrets
# Manager + an external serverless Postgres (Neon). No EKS, no NAT, no ALB.
#
# Run DB migrations once against database_url before/after first apply.
###############################################################################

data "aws_caller_identity" "me" {}

locals {
  services      = ["runtime", "registry", "portal"]
  encryption_key           = var.encryption_key != "" ? var.encryption_key : random_password.encryption_key.result
  nextauth_secret          = var.nextauth_secret != "" ? var.nextauth_secret : random_password.nextauth_secret.result
  router_rs256_private_key = var.router_rs256_private_key != "" ? var.router_rs256_private_key : tls_private_key.router.private_key_pem
  nextauth_rs256_key       = tls_private_key.router.private_key_pem_pkcs8

  # Secrets pushed to Secrets Manager, referenced by App Runner env.
  secrets = merge(
    {
      DATABASE_URL               = var.database_url
      ENCRYPTION_KEY             = local.encryption_key
      ROUTER_RS256_PRIVATE_KEY   = local.router_rs256_private_key
      NEXTAUTH_RS256_PRIVATE_KEY = local.nextauth_rs256_key
      NEXTAUTH_SECRET            = local.nextauth_secret
    },
    var.license_key != "" ? { SEALFLEET_LICENSE_KEY = var.license_key } : {},
    var.stripe_secret_key != "" ? { STRIPE_SECRET_KEY = var.stripe_secret_key } : {},
    var.stripe_webhook_secret != "" ? { STRIPE_WEBHOOK_SECRET = var.stripe_webhook_secret } : {},
  )

  portal_url = var.portal_domain != "" ? "https://${var.portal_domain}" : ""
}

resource "random_password" "encryption_key" {
  length  = 44
  special = false
}
resource "random_password" "nextauth_secret" {
  length  = 32
  special = false
}
resource "tls_private_key" "router" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

# --- ECR: one repo per service (mirror the sealfleet-* images here) ----------
resource "aws_ecr_repository" "svc" {
  for_each             = toset(local.services)
  name                 = "${var.name}-${each.value}"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
}

# --- Secrets Manager ---------------------------------------------------------
resource "aws_secretsmanager_secret" "s" {
  for_each                = local.secrets
  name                    = "${var.name}/${each.key}"
  recovery_window_in_days = 0
}
resource "aws_secretsmanager_secret_version" "v" {
  for_each      = local.secrets
  secret_id     = aws_secretsmanager_secret.s[each.key].id
  secret_string = each.value
}

# --- IAM: App Runner ECR access role + instance (task) role ------------------
data "aws_iam_policy_document" "apprunner_build_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["build.apprunner.amazonaws.com"]
    }
  }
}
resource "aws_iam_role" "ecr_access" {
  name               = "${var.name}-apprunner-ecr"
  assume_role_policy = data.aws_iam_policy_document.apprunner_build_assume.json
}
resource "aws_iam_role_policy_attachment" "ecr_access" {
  role       = aws_iam_role.ecr_access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

data "aws_iam_policy_document" "apprunner_task_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["tasks.apprunner.amazonaws.com"]
    }
  }
}
resource "aws_iam_role" "instance" {
  name               = "${var.name}-apprunner-instance"
  assume_role_policy = data.aws_iam_policy_document.apprunner_task_assume.json
}
data "aws_iam_policy_document" "instance" {
  statement {
    sid       = "ReadSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [for s in aws_secretsmanager_secret.s : s.arn]
  }
  statement {
    sid       = "Marketplace"
    actions   = ["aws-marketplace:ResolveCustomer", "aws-marketplace:GetEntitlements"]
    resources = ["*"]
  }
}
resource "aws_iam_role_policy" "instance" {
  name   = "${var.name}-instance"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.instance.json
}

# --- Autoscaling (min>=1; App Runner has no scale-to-zero) --------------------
resource "aws_apprunner_auto_scaling_configuration_version" "asc" {
  auto_scaling_configuration_name = var.name
  min_size                        = var.min_size
  max_size                        = var.max_size
}

# Helper: secret env references for a set of secret keys.
locals {
  secret_arns = { for k, s in aws_secretsmanager_secret.s : k => s.arn }
}

# --- Registry service --------------------------------------------------------
resource "aws_apprunner_service" "registry" {
  service_name = "${var.name}-registry"
  source_configuration {
    authentication_configuration { access_role_arn = aws_iam_role.ecr_access.arn }
    image_repository {
      image_identifier      = "${aws_ecr_repository.svc["registry"].repository_url}:${var.image_tag}"
      image_repository_type = "ECR"
      image_configuration {
        port                          = "8080"
        runtime_environment_variables = { REQUIRE_AUTH = "true" }
        runtime_environment_secrets   = { DATABASE_URL = local.secret_arns["DATABASE_URL"] }
      }
    }
    auto_deployments_enabled = false
  }
  instance_configuration {
    cpu               = var.cpu
    memory            = var.memory
    instance_role_arn = aws_iam_role.instance.arn
  }
  auto_scaling_configuration_arn = aws_apprunner_auto_scaling_configuration_version.asc.arn
  depends_on                     = [aws_secretsmanager_secret_version.v]
}

# --- Router (runtime) service ------------------------------------------------
resource "aws_apprunner_service" "router" {
  service_name = "${var.name}-router"
  source_configuration {
    authentication_configuration { access_role_arn = aws_iam_role.ecr_access.arn }
    image_repository {
      image_identifier      = "${aws_ecr_repository.svc["runtime"].repository_url}:${var.image_tag}"
      image_repository_type = "ECR"
      image_configuration {
        port = "8080"
        runtime_environment_variables = {
          REQUIRE_AUTH              = "true"
          AUTH_ALLOW_EPHEMERAL_KEYS = "false"
          MCPFINDER_DEPLOYMENT_ENV  = "production"
          REGISTRY_URL              = "https://${aws_apprunner_service.registry.service_url}"
          PORTAL_URL               = local.portal_url
        }
        runtime_environment_secrets = merge({
          DATABASE_URL             = local.secret_arns["DATABASE_URL"]
          ENCRYPTION_KEY           = local.secret_arns["ENCRYPTION_KEY"]
          ROUTER_RS256_PRIVATE_KEY = local.secret_arns["ROUTER_RS256_PRIVATE_KEY"]
          }, var.license_key != "" ? { SEALFLEET_LICENSE_KEY = local.secret_arns["SEALFLEET_LICENSE_KEY"] } : {})
      }
    }
    auto_deployments_enabled = false
  }
  instance_configuration {
    cpu               = var.cpu
    memory            = var.memory
    instance_role_arn = aws_iam_role.instance.arn
  }
  auto_scaling_configuration_arn = aws_apprunner_auto_scaling_configuration_version.asc.arn
  depends_on                     = [aws_secretsmanager_secret_version.v]
}

# --- Portal service ----------------------------------------------------------
resource "aws_apprunner_service" "portal" {
  service_name = "${var.name}-portal"
  source_configuration {
    authentication_configuration { access_role_arn = aws_iam_role.ecr_access.arn }
    image_repository {
      image_identifier      = "${aws_ecr_repository.svc["portal"].repository_url}:${var.image_tag}"
      image_repository_type = "ECR"
      image_configuration {
        port = "8080"
        runtime_environment_variables = merge({
          NODE_ENV         = "production"
          ROUTER_URL       = "https://${aws_apprunner_service.router.service_url}"
          NEXTAUTH_URL     = local.portal_url
          AUTH_URL         = local.portal_url
          PORTAL_PUBLIC_URL = local.portal_url
          AWS_MARKETPLACE_REGION = var.region
          }, var.aws_marketplace_product_code != "" ? {
          SEALFLEET_AWS_MARKETPLACE_PRODUCT_CODE = var.aws_marketplace_product_code
          # awsMarketplaceConfigured() gates on AWS_ROLE_ARN (or access keys).
          # The SDK still sources creds from the App Runner instance role at
          # runtime; this env just flips the "configured" check on.
          AWS_ROLE_ARN = aws_iam_role.instance.arn
        } : {})
        runtime_environment_secrets = merge({
          DATABASE_URL               = local.secret_arns["DATABASE_URL"]
          NEXTAUTH_SECRET            = local.secret_arns["NEXTAUTH_SECRET"]
          NEXTAUTH_RS256_PRIVATE_KEY = local.secret_arns["NEXTAUTH_RS256_PRIVATE_KEY"]
          }, var.stripe_secret_key != "" ? { STRIPE_SECRET_KEY = local.secret_arns["STRIPE_SECRET_KEY"] } : {},
          var.stripe_webhook_secret != "" ? { STRIPE_WEBHOOK_SECRET = local.secret_arns["STRIPE_WEBHOOK_SECRET"] } : {})
      }
    }
    auto_deployments_enabled = false
  }
  instance_configuration {
    cpu               = var.cpu
    memory            = var.memory
    instance_role_arn = aws_iam_role.instance.arn
  }
  auto_scaling_configuration_arn = aws_apprunner_auto_scaling_configuration_version.asc.arn
  depends_on                     = [aws_secretsmanager_secret_version.v]
}

# --- Custom domain for the portal (optional) ---------------------------------
resource "aws_apprunner_custom_domain_association" "portal" {
  count       = var.portal_domain != "" ? 1 : 0
  domain_name = var.portal_domain
  service_arn = aws_apprunner_service.portal.arn
}

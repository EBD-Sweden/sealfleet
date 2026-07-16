###############################################################################
# Sealfleet on AWS — REFERENCE module.
#
# Provisions EKS + RDS Postgres + Secrets Manager (KMS-encrypted) + the AWS Load
# Balancer Controller (ALB ingress), then installs the mcpfinder Helm chart.
#
# REFERENCE-GRADE: variables/outputs/wiring are complete and the module is
# fmt/validate-clean, but it has NOT been apply-tested against a live AWS account
# (no cloud creds in this environment). Review IAM scope, instance sizing, and
# the AWS LB Controller IRSA wiring before applying. To keep the dependency
# surface small and inspectable this module uses the community terraform-aws-eks
# / -vpc / -rds modules; pin them to a reviewed version before production.
###############################################################################

locals {
  tags = merge({
    "app.kubernetes.io/part-of" = "mcpfinder"
    "managed-by"                = "terraform"
  }, var.tags)

  # Resolve secret values: use provided values, else generated ones.
  encryption_key           = var.encryption_key != "" ? var.encryption_key : random_password.encryption_key.result
  nextauth_secret          = var.nextauth_secret != "" ? var.nextauth_secret : random_password.nextauth_secret.result
  router_rs256_private_key = var.router_rs256_private_key != "" ? var.router_rs256_private_key : tls_private_key.router.private_key_pem

  database_url = format(
    "postgresql://%s:%s@%s/%s?sslmode=require",
    var.db_username,
    random_password.db.result,
    module.rds.db_instance_endpoint,
    var.db_name,
  )
}

# ----------------------------------------------------------------------------
# Generated secrets (used when the caller does not supply explicit values).
# ----------------------------------------------------------------------------
resource "random_password" "db" {
  length  = 32
  special = false
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

# Enforce a safe API-endpoint posture: a public endpoint must be CIDR-scoped.
resource "terraform_data" "public_access_guard" {
  lifecycle {
    precondition {
      condition     = !var.cluster_endpoint_public_access || length(var.cluster_public_access_cidrs) > 0
      error_message = "cluster_public_access_cidrs must list your operator CIDRs when cluster_endpoint_public_access is true (or set cluster_endpoint_public_access=false for a private cluster)."
    }
  }
}

# ----------------------------------------------------------------------------
# VPC
# ----------------------------------------------------------------------------
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = "${var.name}-vpc"
  cidr = var.vpc_cidr
  azs  = var.azs

  private_subnets = [for i, az in var.azs : cidrsubnet(var.vpc_cidr, 4, i)]
  public_subnets  = [for i, az in var.azs : cidrsubnet(var.vpc_cidr, 4, i + 8)]

  enable_nat_gateway   = true
  single_nat_gateway   = true
  enable_dns_hostnames = true

  # Tags required by the AWS Load Balancer Controller for subnet discovery.
  public_subnet_tags  = { "kubernetes.io/role/elb" = "1" }
  private_subnet_tags = { "kubernetes.io/role/internal-elb" = "1" }

  tags = local.tags
}

# ----------------------------------------------------------------------------
# EKS cluster + managed node group
# ----------------------------------------------------------------------------
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = var.name
  cluster_version = var.kubernetes_version

  # The Kubernetes API endpoint. Private access is always on; public access is
  # restricted to an explicit CIDR allowlist (default: none — set
  # cluster_public_access_cidrs to your operator IPs, or disable public access
  # entirely and reach the API over the VPC / a bastion).
  cluster_endpoint_private_access      = true
  cluster_endpoint_public_access       = var.cluster_endpoint_public_access
  cluster_endpoint_public_access_cidrs = var.cluster_public_access_cidrs

  # Control-plane audit + component logs to CloudWatch (SOC2 CC7.2 evidence).
  cluster_enabled_log_types = ["api", "audit", "authenticator", "controllerManager", "scheduler"]

  # Envelope-encrypt Kubernetes Secrets at rest with a dedicated CMK.
  cluster_encryption_config = {
    provider_key_arn = aws_kms_key.eks.arn
    resources        = ["secrets"]
  }

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  enable_irsa = true

  eks_managed_node_groups = {
    default = {
      instance_types = var.node_instance_types
      desired_size   = var.node_desired_size
      min_size       = var.node_min_size
      max_size       = var.node_max_size
    }
  }

  tags = local.tags
}

# CMK for EKS Secret envelope encryption.
resource "aws_kms_key" "eks" {
  description             = "${var.name} EKS secrets envelope key"
  deletion_window_in_days = 14
  enable_key_rotation     = true
  tags                    = local.tags
}

# ----------------------------------------------------------------------------
# RDS Postgres + KMS
# ----------------------------------------------------------------------------
resource "aws_kms_key" "rds" {
  description             = "${var.name} RDS encryption key"
  deletion_window_in_days = 14
  enable_key_rotation     = true
  tags                    = local.tags
}

resource "aws_security_group" "rds" {
  name        = "${var.name}-rds"
  description = "Allow Postgres from the EKS node security group only."
  vpc_id      = module.vpc.vpc_id

  ingress {
    description     = "Postgres from EKS nodes"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id]
  }

  # RDS is a connection target, not an initiator — no egress required. Omitting
  # the egress block leaves the SG with no outbound rules (default-deny), which
  # avoids the unrestricted 0.0.0.0/0 egress a broad rule would grant.

  tags = local.tags
}

module "rds" {
  source  = "terraform-aws-modules/rds/aws"
  version = "~> 6.0"

  identifier = var.name

  engine               = "postgres"
  engine_version       = var.db_engine_version
  family               = "postgres16"
  major_engine_version = "16"
  instance_class       = var.db_instance_class

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_allocated_storage * 4

  db_name  = var.db_name
  username = var.db_username
  password = random_password.db.result
  port     = 5432

  manage_master_user_password = false

  multi_az               = var.db_multi_az
  storage_encrypted      = true
  kms_key_id             = aws_kms_key.rds.arn
  vpc_security_group_ids = [aws_security_group.rds.id]
  create_db_subnet_group = true
  subnet_ids             = module.vpc.private_subnets

  backup_retention_period = var.db_backup_retention_days
  deletion_protection     = var.db_deletion_protection
  skip_final_snapshot     = false

  tags = local.tags
}

# ----------------------------------------------------------------------------
# Secrets Manager — KMS-encrypted platform secret consumed by the chart.
# The chart references it via secrets.existingSecret; the External Secrets
# Operator (or the Secrets Store CSI driver) syncs it into a k8s Secret. This
# module creates the AWS-side secret; install ESO/CSI separately (see README).
# ----------------------------------------------------------------------------
resource "aws_kms_key" "secrets" {
  description             = "${var.name} platform secrets key"
  deletion_window_in_days = 14
  enable_key_rotation     = true
  tags                    = local.tags
}

resource "aws_secretsmanager_secret" "platform" {
  name       = "${var.name}/platform"
  kms_key_id = aws_kms_key.secrets.arn
  tags       = local.tags
}

resource "aws_secretsmanager_secret_version" "platform" {
  secret_id = aws_secretsmanager_secret.platform.id
  secret_string = jsonencode({
    DATABASE_URL             = local.database_url
    ENCRYPTION_KEY           = local.encryption_key
    ROUTER_RS256_PRIVATE_KEY = local.router_rs256_private_key
    NEXTAUTH_SECRET          = local.nextauth_secret
  })
}

# ----------------------------------------------------------------------------
# AWS Load Balancer Controller (ALB ingress) via Helm.
# IRSA role/policy wiring is provided by the community iam-role-for-service-
# accounts submodule; review the attached policy before production.
# ----------------------------------------------------------------------------
module "lb_controller_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.39"

  role_name                              = "${var.name}-aws-lb-controller"
  attach_load_balancer_controller_policy = true

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["kube-system:aws-load-balancer-controller"]
    }
  }

  tags = local.tags
}

resource "helm_release" "aws_lb_controller" {
  count = var.install_helm_release ? 1 : 0

  name       = "aws-load-balancer-controller"
  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-load-balancer-controller"
  namespace  = "kube-system"

  set {
    name  = "clusterName"
    value = module.eks.cluster_name
  }
  set {
    name  = "serviceAccount.create"
    value = "true"
  }
  set {
    name  = "serviceAccount.name"
    value = "aws-load-balancer-controller"
  }
  set {
    name  = "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = module.lb_controller_irsa.iam_role_arn
  }

  depends_on = [module.eks]
}

# ----------------------------------------------------------------------------
# Sealfleet Helm release.
#
# Postgres is external (RDS). The platform Secret is created in-cluster from the
# generated/provided values here (secrets.create=true) so the release is
# self-contained for a reference deploy. For a stricter posture, set
# install_helm_release=false, sync the Secrets Manager secret via ESO, and pass
# secrets.create=false + secrets.existingSecret to the chart.
# ----------------------------------------------------------------------------
resource "helm_release" "mcpfinder" {
  count = var.install_helm_release ? 1 : 0

  name             = var.release_name
  namespace        = var.release_namespace
  create_namespace = true
  chart            = var.helm_chart_path

  values = [yamlencode({
    image = {
      registry = var.image_registry
      tag      = var.image_tag
    }
    postgresql = { enabled = false }
    secrets = {
      create                = true
      databaseUrl           = local.database_url
      encryptionKey         = local.encryption_key
      routerRs256PrivateKey = local.router_rs256_private_key
      nextauthSecret        = local.nextauth_secret
    }
    router = {
      requireAuth        = true
      allowEphemeralKeys = false
    }
    ingress = {
      enabled       = true
      className     = "alb"
      tlsSecretName = ""
      annotations = merge(
        {
          "alb.ingress.kubernetes.io/scheme"       = "internet-facing"
          "alb.ingress.kubernetes.io/target-type"  = "ip"
          "alb.ingress.kubernetes.io/listen-ports" = jsonencode([{ HTTPS = 443 }, { HTTP = 80 }])
          "alb.ingress.kubernetes.io/ssl-redirect" = "443"
        },
        var.acm_certificate_arn != "" ? {
          "alb.ingress.kubernetes.io/certificate-arn" = var.acm_certificate_arn
        } : {}
      )
      hosts = {
        portal   = var.ingress_hosts.portal
        router   = var.ingress_hosts.router
        deploy   = var.ingress_hosts.deploy
        registry = var.ingress_hosts.registry
      }
      tls = {
        # ALB terminates TLS via the ACM cert (annotation); no in-cluster TLS Secret.
        enabled     = false
        certManager = { enabled = false }
      }
    }
  })]

  depends_on = [
    module.rds,
    helm_release.aws_lb_controller,
  ]
}

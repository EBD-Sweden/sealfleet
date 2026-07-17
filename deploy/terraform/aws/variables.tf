variable "region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "eu-north-1"
}

variable "name" {
  description = "Name prefix for all resources."
  type        = string
  default     = "mcpfinder"
}

variable "tags" {
  description = "Common tags applied to all resources."
  type        = map(string)
  default     = {}
}

# --- Networking ------------------------------------------------------------

variable "vpc_cidr" {
  description = "CIDR for the VPC created for the cluster."
  type        = string
  default     = "10.60.0.0/16"
}

variable "azs" {
  description = "Availability zones to spread subnets across."
  type        = list(string)
  default     = ["eu-north-1a", "eu-north-1b", "eu-north-1c"]
}

# --- EKS -------------------------------------------------------------------

variable "kubernetes_version" {
  description = "EKS control-plane Kubernetes version."
  type        = string
  default     = "1.30"
}

variable "cluster_endpoint_public_access" {
  description = "Expose the Kubernetes API endpoint publicly (restricted by cluster_public_access_cidrs). Set false to keep the API private-only."
  type        = bool
  default     = true
}

variable "cluster_public_access_cidrs" {
  description = "CIDRs allowed to reach the public Kubernetes API endpoint. Required (and must not be 0.0.0.0/0) when cluster_endpoint_public_access is true — set your operator/CI egress IPs, e.g. [\"203.0.113.4/32\"]. Ignored when public access is disabled."
  type        = list(string)
  default     = []

  validation {
    condition     = !contains(var.cluster_public_access_cidrs, "0.0.0.0/0")
    error_message = "Refusing 0.0.0.0/0: the public Kubernetes API must be restricted to specific CIDRs (or disable cluster_endpoint_public_access for a private cluster)."
  }
}

variable "node_instance_types" {
  description = "Instance types for the managed node group."
  type        = list(string)
  default     = ["m6i.large"]
}

variable "node_desired_size" {
  description = "Desired node count."
  type        = number
  default     = 3
}

variable "node_min_size" {
  description = "Minimum node count."
  type        = number
  default     = 2
}

variable "node_max_size" {
  description = "Maximum node count."
  type        = number
  default     = 6
}

# --- RDS Postgres ----------------------------------------------------------

variable "db_engine_version" {
  description = "Postgres engine version for RDS."
  type        = string
  default     = "16.4"
}

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t3.medium"
}

variable "db_allocated_storage" {
  description = "RDS allocated storage (GiB)."
  type        = number
  default     = 50
}

variable "db_name" {
  description = "Initial database name."
  type        = string
  default     = "mcpfinder"
}

variable "db_username" {
  description = "Master DB username."
  type        = string
  default     = "mcpfinder"
}

variable "db_multi_az" {
  description = "Enable Multi-AZ for RDS (recommended in prod)."
  type        = bool
  default     = true
}

variable "db_backup_retention_days" {
  description = "Automated backup retention in days."
  type        = number
  default     = 14
}

variable "db_deletion_protection" {
  description = "Protect the RDS instance from accidental deletion."
  type        = bool
  default     = true
}

# --- Helm release / images -------------------------------------------------

variable "helm_chart_path" {
  description = "Path to the mcpfinder Helm chart (relative to this module)."
  type        = string
  default     = "../../helm/sealfleet"
}

variable "release_name" {
  description = "Helm release name."
  type        = string
  default     = "mcpfinder"
}

variable "release_namespace" {
  description = "Namespace to install the release into."
  type        = string
  default     = "mcpfinder"
}

variable "image_registry" {
  description = "Container image registry/repo prefix for the platform images."
  type        = string
  default     = "ghcr.io/ebd-sweden"
}

variable "image_tag" {
  description = "Image tag for all services. Pin to a release; avoid 'latest' in prod."
  type        = string
  default     = "0.2.0"
}

variable "ingress_hosts" {
  description = "Public hostnames per service for the ALB ingress."
  type = object({
    portal   = string
    router   = string
    deploy   = string
    registry = string
  })
  default = {
    portal   = "portal.sealfleet.example.com"
    router   = "router.sealfleet.example.com"
    deploy   = "deploy.sealfleet.example.com"
    registry = "registry.sealfleet.example.com"
  }
}

variable "acm_certificate_arn" {
  description = "ACM certificate ARN for ALB TLS. Leave empty to skip TLS wiring (NOT recommended)."
  type        = string
  default     = ""
}

variable "install_helm_release" {
  description = "Whether Terraform should install the Helm chart after the cluster + RDS exist."
  type        = bool
  default     = true
}

variable "encryption_key" {
  description = "Fernet ENCRYPTION_KEY for the broker. If empty a random secret is generated and stored in Secrets Manager."
  type        = string
  default     = ""
  sensitive   = true
}

variable "router_rs256_private_key" {
  description = "RS256 private key (PEM) for the router. If empty a 2048-bit RSA key is generated."
  type        = string
  default     = ""
  sensitive   = true
}

variable "nextauth_secret" {
  description = "NextAuth session secret for the portal. If empty a random secret is generated."
  type        = string
  default     = ""
  sensitive   = true
}

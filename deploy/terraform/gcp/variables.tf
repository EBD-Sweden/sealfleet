variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "region" {
  description = "GCP region."
  type        = string
  default     = "europe-north1"
}

variable "name" {
  description = "Name prefix for all resources."
  type        = string
  default     = "mcpfinder"
}

# --- Networking ------------------------------------------------------------

variable "subnet_cidr" {
  description = "Primary subnet CIDR for the GKE node range."
  type        = string
  default     = "10.70.0.0/20"
}

variable "pods_cidr" {
  description = "Secondary range CIDR for GKE pods."
  type        = string
  default     = "10.71.0.0/16"
}

variable "services_cidr" {
  description = "Secondary range CIDR for GKE services."
  type        = string
  default     = "10.72.0.0/20"
}

# --- GKE -------------------------------------------------------------------

# GKE version is governed by the REGULAR release channel (see main.tf); there is
# no version-pin variable (a partial alias conflicts with the channel).

variable "master_cidr" {
  description = "RFC1918 /28 for the GKE private control-plane peering range."
  type        = string
  default     = "172.16.0.0/28"
}

variable "master_authorized_cidrs" {
  description = "CIDRs allowed to reach the GKE API server (GCP-0061). Empty = GCP-internal only; add your operator/CI egress IPs to run terraform apply, e.g. [{ cidr = \"203.0.113.4/32\", name = \"ci\" }]."
  type        = list(object({ cidr = string, name = string }))
  default     = []
}

variable "cluster_deletion_protection" {
  description = "Protect the GKE cluster from terraform destroy."
  type        = bool
  default     = true
}

variable "labels" {
  description = "Extra resource labels merged onto the cluster."
  type        = map(string)
  default     = {}
}

variable "node_machine_type" {
  description = "Machine type for the node pool."
  type        = string
  default     = "e2-standard-4"
}

variable "node_count" {
  description = "Nodes per zone in the node pool."
  type        = number
  default     = 1
}

variable "node_min_count" {
  description = "Autoscaling minimum nodes per zone."
  type        = number
  default     = 1
}

variable "node_max_count" {
  description = "Autoscaling maximum nodes per zone."
  type        = number
  default     = 3
}

# --- Cloud SQL Postgres ----------------------------------------------------

variable "db_version" {
  description = "Cloud SQL Postgres version."
  type        = string
  default     = "POSTGRES_16"
}

variable "db_tier" {
  description = "Cloud SQL machine tier."
  type        = string
  default     = "db-custom-2-7680"
}

variable "db_name" {
  description = "Initial database name."
  type        = string
  default     = "mcpfinder"
}

variable "db_username" {
  description = "Database user name."
  type        = string
  default     = "mcpfinder"
}

variable "db_availability_type" {
  description = "REGIONAL (HA) or ZONAL. Use REGIONAL in prod."
  type        = string
  default     = "REGIONAL"
}

variable "db_deletion_protection" {
  description = "Protect the Cloud SQL instance from deletion."
  type        = bool
  default     = true
}

variable "db_disk_size" {
  description = "Cloud SQL disk size (GiB)."
  type        = number
  default     = 50
}

# --- Helm release / images -------------------------------------------------

variable "helm_chart_path" {
  description = "Path to the mcpfinder Helm chart (relative to this module)."
  type        = string
  default     = ""
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
  default     = "0.3.0"
}

variable "ingress_hosts" {
  description = "Public hostnames per service for the GCE ingress."
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

variable "managed_certificate_domains" {
  description = "Domains for a Google-managed certificate (used by the GCE ingress). Empty = skip managed cert."
  type        = list(string)
  default     = []
}

variable "install_helm_release" {
  description = "Whether Terraform should install the Helm chart after the cluster + Cloud SQL exist."
  type        = bool
  default     = true
}

variable "encryption_key" {
  description = "Fernet ENCRYPTION_KEY for the broker. If empty a random secret is generated and stored in Secret Manager."
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

variable "region" {
  description = "AWS region (App Runner + ECR + Secrets Manager)."
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Name prefix for resources."
  type        = string
  default     = "sealfleet"
}

variable "image_tag" {
  description = "Image tag deployed to App Runner. Images are mirrored into this account's ECR (see README)."
  type        = string
  default     = "0.7.0"
}

# --- Database (serverless Postgres, external — Neon/Supabase) ---------------
# A SEPARATE Neon project from the GCP deployment: AWS Marketplace buyers are
# tenants here, direct/GCP buyers are tenants there.
variable "database_url" {
  description = "Postgres connection string for the AWS-hosted instance (pooled, sslmode=require). Use a serverless Postgres (Neon)."
  type        = string
  sensitive   = true
}

# --- Scaling ----------------------------------------------------------------
variable "min_size" {
  description = "App Runner minimum instances per service (>=1; App Runner has no scale-to-zero)."
  type        = number
  default     = 1
}

variable "max_size" {
  description = "App Runner maximum instances per service."
  type        = number
  default     = 5
}

variable "cpu" {
  description = "App Runner CPU per service (e.g. 256, 512, 1024)."
  type        = string
  default     = "512"
}

variable "memory" {
  description = "App Runner memory per service (e.g. 512, 1024, 2048)."
  type        = string
  default     = "1024"
}

# --- Public URL / domain ----------------------------------------------------
variable "portal_domain" {
  description = "Custom domain for the portal (e.g. aws.sealfleet.ebdsweden.com). Empty = use the App Runner default *.awsapprunner.com URL."
  type        = string
  default     = ""
}

# --- Platform secrets (generated when empty) --------------------------------
variable "encryption_key" {
  type      = string
  default   = ""
  sensitive = true
}
variable "router_rs256_private_key" {
  type      = string
  default   = ""
  sensitive = true
}
variable "nextauth_secret" {
  type      = string
  default   = ""
  sensitive = true
}

# --- Licensing --------------------------------------------------------------
variable "license_key" {
  description = "Sealfleet Enterprise license key (unlocks SSO/SCIM etc.)."
  type        = string
  default     = ""
  sensitive   = true
}

# --- AWS Marketplace fulfillment (portal) -----------------------------------
variable "aws_marketplace_product_code" {
  description = "AWS Marketplace SaaS Product Code. The portal's App Runner instance role is granted ResolveCustomer + GetEntitlements, so no access keys are needed here."
  type        = string
  default     = ""
}

# --- Stripe (optional, if you also take direct card payments here) ----------
variable "stripe_secret_key" {
  type      = string
  default   = ""
  sensitive = true
}
variable "stripe_webhook_secret" {
  type      = string
  default   = ""
  sensitive = true
}

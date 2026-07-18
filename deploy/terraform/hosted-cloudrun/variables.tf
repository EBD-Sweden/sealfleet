variable "project_id" {
  description = "GCP project ID for the hosted Sealfleet deployment."
  type        = string
}

variable "region" {
  description = "Cloud Run region."
  type        = string
  default     = "europe-north1"
}

variable "name" {
  description = "Name prefix for the Cloud Run services + secrets."
  type        = string
  default     = "sealfleet"
}

# --- Images ----------------------------------------------------------------

variable "image_registry" {
  description = "Container image registry/repo prefix."
  type        = string
  default     = "ghcr.io/ebd-sweden"
}

variable "image_tag" {
  description = "Image tag for all services. Pin to a release."
  type        = string
  default     = "0.5.0"
}

# --- Database (serverless Postgres, external — e.g. Neon / Supabase) --------

variable "database_url" {
  description = "Postgres connection string. Use a serverless Postgres that scales to zero (Neon, Supabase) so idle cost stays ~$0. Must be reachable from Cloud Run (sslmode=require)."
  type        = string
  sensitive   = true
}

# --- Scaling (the whole point: min=0 => pay nothing when idle) --------------

variable "min_instances" {
  description = "Minimum Cloud Run instances per service. 0 = scale to zero (no idle cost; first request cold-starts). Set 1 to remove cold starts once you have steady traffic."
  type        = number
  default     = 0
}

variable "max_instances" {
  description = "Maximum Cloud Run instances per service."
  type        = number
  default     = 10
}

# --- Public URL / auth ------------------------------------------------------

variable "portal_public_url" {
  description = "Public URL the portal is served at (used for NEXTAUTH_URL / AUTH_URL). Set to your custom domain (e.g. https://app.sealfleet.ebdsweden.com); if empty, set it after the first apply from the portal's Cloud Run URL."
  type        = string
  default     = ""
}

variable "allow_public_invoke" {
  description = "Allow unauthenticated invocation of the Cloud Run services. Sealfleet's own auth (REQUIRE_AUTH, portal login) protects them; requires org policy to permit allUsers. Set false to require IAM-authenticated invocation."
  type        = bool
  default     = true
}

# --- Platform secrets (generated when empty) --------------------------------

variable "encryption_key" {
  description = "Fernet ENCRYPTION_KEY for the broker. If empty a random secret is generated."
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

# --- Billing (Stripe — direct-billing for the hosted service) ---------------

variable "stripe_secret_key" {
  description = "Stripe secret key (sk_live_… / sk_test_…). Stored in Secret Manager. Empty = billing disabled (portal shows 'not configured')."
  type        = string
  default     = ""
  sensitive   = true
}

variable "stripe_webhook_secret" {
  description = "Stripe webhook signing secret (whsec_…) for /api/billing/webhook. Stored in Secret Manager."
  type        = string
  default     = ""
  sensitive   = true
}

variable "stripe_price_enterprise" {
  description = "Legacy single Stripe Price ID (price_…). Optional fallback if the per-plan IDs below are unset."
  type        = string
  default     = ""
}

variable "stripe_price_hosted_monthly" {
  description = "Stripe Price ID for the Hosted Monthly plan (flat)."
  type        = string
  default     = ""
}

variable "stripe_price_hosted_annual" {
  description = "Stripe Price ID for the Hosted Annual plan (flat)."
  type        = string
  default     = ""
}

variable "stripe_price_hosted_usage" {
  description = "Stripe Price ID for the Hosted Usage-only plan (metered; backed by the meter below)."
  type        = string
  default     = ""
}

variable "stripe_meter_event_name" {
  description = "Stripe Billing Meter event_name the usage reporter emits."
  type        = string
  default     = "sealfleet_api_calls"
}

variable "usage_report_schedule" {
  description = "Cron schedule for the metered-usage reporter (Cloud Scheduler). Empty = don't create the job."
  type        = string
  default     = "0 * * * *" # hourly
}

variable "billing_cron_secret" {
  description = "Shared secret the usage-report Cloud Scheduler job sends as x-billing-cron-secret. If empty a random secret is generated."
  type        = string
  default     = ""
  sensitive   = true
}

# --- Licensing (this hosted service is the Enterprise tier) ------------------

variable "license_public_key" {
  description = "Sealfleet license public key (base64). Baked into released images; override only for self-issued keys."
  type        = string
  default     = ""
}

variable "license_key" {
  description = "Sealfleet Enterprise license key. For the hosted service, provide a key that unlocks the enterprise features you offer (or leave empty and bill per tenant via your own logic)."
  type        = string
  default     = ""
  sensitive   = true
}

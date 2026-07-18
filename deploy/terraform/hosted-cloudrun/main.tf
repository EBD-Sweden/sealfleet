###############################################################################
# Hosted Sealfleet on Cloud Run — SCALE-TO-ZERO managed service.
#
# Runs the platform (router, registry, portal) as Cloud Run services with
# min_instances=0, so you pay ~$0 while idle and only for actual request time.
# New customers are tenants in the shared Postgres (Sealfleet's multi-tenancy) —
# no per-customer infrastructure. Postgres is an external serverless DB (Neon /
# Supabase) so it also scales to zero. See docs/HOSTED.md.
#
# Run DB migrations once against `database_url` before/after first apply
# (see the module README) — this module provisions the services, not the schema.
###############################################################################

locals {
  encryption_key           = var.encryption_key != "" ? var.encryption_key : random_password.encryption_key.result
  nextauth_secret          = var.nextauth_secret != "" ? var.nextauth_secret : random_password.nextauth_secret.result
  router_rs256_private_key = var.router_rs256_private_key != "" ? var.router_rs256_private_key : tls_private_key.router.private_key_pem
  billing_cron_secret      = var.billing_cron_secret != "" ? var.billing_cron_secret : random_password.billing_cron_secret.result
  image                    = { for s in ["runtime", "registry", "portal"] : s => "${var.image_registry}/sealfleet-${s}:${var.image_tag}" }
  # Create the usage-reporter schedule only if billing is on and a schedule is set.
  usage_reporter_enabled = var.stripe_secret_key != "" && var.stripe_price_hosted_usage != "" && var.usage_report_schedule != ""
}

resource "random_password" "encryption_key" {
  length  = 44
  special = false
}

resource "random_password" "nextauth_secret" {
  length  = 32
  special = false
}

resource "random_password" "billing_cron_secret" {
  length  = 32
  special = false
}

resource "tls_private_key" "router" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

# ---------------------------------------------------------------------------
# Secrets (Secret Manager) consumed by the services as env references.
# ---------------------------------------------------------------------------
locals {
  secrets = {
    DATABASE_URL             = var.database_url
    ENCRYPTION_KEY           = local.encryption_key
    ROUTER_RS256_PRIVATE_KEY = local.router_rs256_private_key
    NEXTAUTH_SECRET          = local.nextauth_secret
    BILLING_CRON_SECRET      = local.billing_cron_secret
  }
}

resource "google_secret_manager_secret" "s" {
  for_each  = local.secrets
  secret_id = "${var.name}-${lower(replace(each.key, "_", "-"))}"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "v" {
  for_each    = local.secrets
  secret      = google_secret_manager_secret.s[each.key].id
  secret_data = each.value
}

# Cloud Run's runtime service account must be able to read the secrets.
data "google_project" "this" {}

resource "google_secret_manager_secret_iam_member" "accessor" {
  for_each  = local.secrets
  secret_id = google_secret_manager_secret.s[each.key].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_project.this.number}-compute@developer.gserviceaccount.com"
}

# Optional Stripe secrets — created only when provided (empty => billing off).
# for_each must be non-sensitive, so drive it off a presence check (the *keys*
# are static strings) and pull the sensitive value by key for secret_data.
locals {
  stripe_values = {
    STRIPE_SECRET_KEY     = var.stripe_secret_key
    STRIPE_WEBHOOK_SECRET = var.stripe_webhook_secret
  }
  stripe_keys = toset(concat(
    nonsensitive(var.stripe_secret_key != "") ? ["STRIPE_SECRET_KEY"] : [],
    nonsensitive(var.stripe_webhook_secret != "") ? ["STRIPE_WEBHOOK_SECRET"] : [],
  ))
}

resource "google_secret_manager_secret" "stripe" {
  for_each  = local.stripe_keys
  secret_id = "${var.name}-${lower(replace(each.value, "_", "-"))}"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "stripe" {
  for_each    = local.stripe_keys
  secret      = google_secret_manager_secret.stripe[each.value].id
  secret_data = local.stripe_values[each.value]
}

resource "google_secret_manager_secret_iam_member" "stripe_accessor" {
  for_each  = local.stripe_keys
  secret_id = google_secret_manager_secret.stripe[each.value].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_project.this.number}-compute@developer.gserviceaccount.com"
}

# ---------------------------------------------------------------------------
# Registry (discovery) — internal, scale-to-zero.
# ---------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "registry" {
  name     = "${var.name}-registry"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }
    containers {
      image = local.image["registry"]
      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.s["DATABASE_URL"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name  = "REQUIRE_AUTH"
        value = "true"
      }
    }
  }
  depends_on = [google_secret_manager_secret_version.v]
}

# ---------------------------------------------------------------------------
# Router (runtime) — the API, scale-to-zero.
# ---------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "router" {
  name     = "${var.name}-router"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }
    containers {
      image = local.image["runtime"]

      dynamic "env" {
        for_each = { DATABASE_URL = "DATABASE_URL", ENCRYPTION_KEY = "ENCRYPTION_KEY", ROUTER_RS256_PRIVATE_KEY = "ROUTER_RS256_PRIVATE_KEY" }
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.s[env.value].secret_id
              version = "latest"
            }
          }
        }
      }
      env {
        name  = "REQUIRE_AUTH"
        value = "true"
      }
      env {
        name  = "AUTH_ALLOW_EPHEMERAL_KEYS"
        value = "false"
      }
      env {
        name  = "MCPFINDER_DEPLOYMENT_ENV"
        value = "production"
      }
      env {
        name  = "REGISTRY_URL"
        value = google_cloud_run_v2_service.registry.uri
      }
      env {
        name  = "PORTAL_URL"
        value = var.portal_public_url
      }
      dynamic "env" {
        for_each = var.license_public_key != "" ? { SEALFLEET_LICENSE_PUBKEY = var.license_public_key } : {}
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = var.license_key != "" ? { SEALFLEET_LICENSE_KEY = var.license_key } : {}
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }
  depends_on = [google_secret_manager_secret_version.v]
}

# ---------------------------------------------------------------------------
# Portal (UI) — public, scale-to-zero.
# ---------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "portal" {
  name     = "${var.name}-portal"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }
    containers {
      image = local.image["portal"]
      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.s["DATABASE_URL"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "NEXTAUTH_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.s["NEXTAUTH_SECRET"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name  = "ROUTER_URL"
        value = google_cloud_run_v2_service.router.uri
      }
      env {
        name  = "NEXTAUTH_URL"
        value = var.portal_public_url != "" ? var.portal_public_url : ""
      }
      env {
        name  = "AUTH_URL"
        value = var.portal_public_url != "" ? var.portal_public_url : ""
      }
      env {
        name  = "PORTAL_PUBLIC_URL"
        value = var.portal_public_url != "" ? var.portal_public_url : ""
      }
      # Stripe billing (portal-side). Secrets injected only when provided;
      # the price ID is not secret.
      dynamic "env" {
        for_each = local.stripe_keys
        content {
          name = env.value
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.stripe[env.value].secret_id
              version = "latest"
            }
          }
        }
      }
      # Plan price IDs + meter name (not secret) — each present only when set.
      dynamic "env" {
        for_each = { for k, v in {
          STRIPE_PRICE_ENTERPRISE     = var.stripe_price_enterprise
          STRIPE_PRICE_HOSTED_MONTHLY = var.stripe_price_hosted_monthly
          STRIPE_PRICE_HOSTED_ANNUAL  = var.stripe_price_hosted_annual
          STRIPE_PRICE_HOSTED_USAGE   = var.stripe_price_hosted_usage
          STRIPE_METER_EVENT_NAME     = var.stripe_meter_event_name
        } : k => v if v != "" }
        content {
          name  = env.key
          value = env.value
        }
      }
      # Shared secret for the usage-report cron endpoint.
      env {
        name = "BILLING_CRON_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.s["BILLING_CRON_SECRET"].secret_id
            version = "latest"
          }
        }
      }
    }
  }
  depends_on = [google_secret_manager_secret_version.v, google_secret_manager_secret_version.stripe]
}

# ---------------------------------------------------------------------------
# Usage reporter — Cloud Scheduler POSTs the portal's report-usage endpoint on a
# schedule; the endpoint aggregates api_key_usage_log and pushes Stripe meter
# events. Created only when metered billing is configured.
# ---------------------------------------------------------------------------
resource "google_cloud_scheduler_job" "usage_reporter" {
  count     = local.usage_reporter_enabled ? 1 : 0
  name      = "${var.name}-usage-reporter"
  region    = var.region
  schedule  = var.usage_report_schedule
  time_zone = "Etc/UTC"

  http_target {
    http_method = "POST"
    uri         = "${google_cloud_run_v2_service.portal.uri}/api/billing/report-usage"
    headers = {
      "x-billing-cron-secret" = local.billing_cron_secret
    }
  }
}

# ---------------------------------------------------------------------------
# Public invocation (app-level auth still applies). Toggle with
# allow_public_invoke; requires org policy to permit allUsers.
# ---------------------------------------------------------------------------
resource "google_cloud_run_v2_service_iam_member" "public" {
  for_each = var.allow_public_invoke ? {
    router   = google_cloud_run_v2_service.router.name
    registry = google_cloud_run_v2_service.registry.name
    portal   = google_cloud_run_v2_service.portal.name
  } : {}
  location = var.region
  name     = each.value
  role     = "roles/run.invoker"
  member   = "allUsers"
}

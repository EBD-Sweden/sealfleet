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
  image                    = { for s in ["runtime", "registry", "portal"] : s => "${var.image_registry}/sealfleet-${s}:${var.image_tag}" }
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

# ---------------------------------------------------------------------------
# Secrets (Secret Manager) consumed by the services as env references.
# ---------------------------------------------------------------------------
locals {
  secrets = {
    DATABASE_URL             = var.database_url
    ENCRYPTION_KEY           = local.encryption_key
    ROUTER_RS256_PRIVATE_KEY = local.router_rs256_private_key
    NEXTAUTH_SECRET          = local.nextauth_secret
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
    }
  }
  depends_on = [google_secret_manager_secret_version.v]
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

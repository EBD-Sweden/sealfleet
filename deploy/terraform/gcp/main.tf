###############################################################################
# Sealfleet on GCP — REFERENCE module.
#
# Provisions a VPC + GKE cluster + Cloud SQL Postgres (private IP) + Secret
# Manager, then installs the mcpfinder Helm chart. Ingress is the native GKE
# (GCE) ingress; a Google-managed certificate can be attached for TLS.
#
# REFERENCE-GRADE: variables/outputs/wiring are complete and the module is
# fmt/validate-clean, but it has NOT been apply-tested against a live GCP project
# (no cloud creds in this environment). Review IAM, machine sizing, and the
# private-services-access peering before applying. Uses native google_* resources
# (no community modules) to keep the surface inspectable.
###############################################################################

locals {
  # Resolve the chart path so remote module sourcing (github.com/...//deploy/
  # terraform/gcp) still finds the bundled chart relative to the module.
  chart_path = var.helm_chart_path != "" ? var.helm_chart_path : "${path.module}/../../helm/sealfleet"

  encryption_key           = var.encryption_key != "" ? var.encryption_key : random_password.encryption_key.result
  nextauth_secret          = var.nextauth_secret != "" ? var.nextauth_secret : random_password.nextauth_secret.result
  router_rs256_private_key = var.router_rs256_private_key != "" ? var.router_rs256_private_key : tls_private_key.router.private_key_pem

  database_url = format(
    "postgresql://%s:%s@%s:5432/%s?sslmode=require",
    var.db_username,
    random_password.db.result,
    google_sql_database_instance.pg.private_ip_address,
    var.db_name,
  )
}

# ----------------------------------------------------------------------------
# Generated secrets
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

# ----------------------------------------------------------------------------
# Network + private services access for Cloud SQL private IP
# ----------------------------------------------------------------------------
resource "google_compute_network" "vpc" {
  name                    = "${var.name}-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "subnet" {
  name          = "${var.name}-subnet"
  ip_cidr_range = var.subnet_cidr
  region        = var.region
  network       = google_compute_network.vpc.id

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = var.pods_cidr
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = var.services_cidr
  }

  private_ip_google_access = true # GCP-0075

  log_config { # GCP-0029 / GCP-0076 — VPC flow logs
    aggregation_interval = "INTERVAL_5_MIN"
    flow_sampling        = 0.5
    metadata             = "INCLUDE_ALL_METADATA"
  }
}

resource "google_compute_global_address" "private_services" {
  name          = "${var.name}-priv-services"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "private_vpc" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_services.name]
}

# ----------------------------------------------------------------------------
# GKE cluster + node pool
# ----------------------------------------------------------------------------
resource "google_container_cluster" "gke" {
  name     = var.name
  location = var.region

  network    = google_compute_network.vpc.id
  subnetwork = google_compute_subnetwork.subnet.id

  # Manage the node pool separately.
  remove_default_node_pool = true
  initial_node_count       = 1

  # Version is governed by the REGULAR release channel below; do not also pin
  # min_master_version (a partial "1.30." alias fights the channel at apply).

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  release_channel {
    channel = "REGULAR"
  }

  # Private nodes (no public IPs); the control-plane keeps a public endpoint
  # restricted to master_authorized_cidrs so the operator/CI can run the apply.
  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
    master_ipv4_cidr_block  = var.master_cidr
  }

  # GCP-0061 — restrict who can reach the Kubernetes API.
  master_authorized_networks_config {
    dynamic "cidr_blocks" {
      for_each = var.master_authorized_cidrs
      content {
        cidr_block   = cidr_blocks.value.cidr
        display_name = cidr_blocks.value.name
      }
    }
  }

  # Dataplane V2 (eBPF) enforces the chart's NetworkPolicies.
  datapath_provider     = "ADVANCED_DATAPATH"
  enable_shielded_nodes = true

  resource_labels = merge({ app = "mcpfinder", managed-by = "terraform" }, var.labels)

  deletion_protection = var.cluster_deletion_protection
}

resource "google_container_node_pool" "primary" {
  name     = "${var.name}-pool"
  location = var.region
  cluster  = google_container_cluster.gke.name

  # initial_node_count + autoscaling (node_count would fight the autoscaler).
  initial_node_count = var.node_count
  lifecycle {
    ignore_changes = [initial_node_count]
  }

  autoscaling {
    min_node_count = var.node_min_count
    max_node_count = var.node_max_count
  }

  node_config {
    machine_type = var.node_machine_type
    image_type   = "COS_CONTAINERD"
    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]

    # GCP-0048 — disable the legacy (v0.1) metadata endpoints.
    metadata = {
      disable-legacy-endpoints = "true"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}

# ----------------------------------------------------------------------------
# Cloud NAT — private nodes have no public IP; NAT lets them pull ghcr.io
# images and lets the migrate Job clone github.com.
# ----------------------------------------------------------------------------
resource "google_compute_router" "nat" {
  name    = "${var.name}-router"
  region  = var.region
  network = google_compute_network.vpc.id
}

resource "google_compute_router_nat" "nat" {
  name                               = "${var.name}-nat"
  router                             = google_compute_router.nat.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# ----------------------------------------------------------------------------
# Cloud SQL Postgres (private IP)
# ----------------------------------------------------------------------------
resource "google_sql_database_instance" "pg" {
  name                = "${var.name}-pg"
  database_version    = var.db_version
  region              = var.region
  deletion_protection = var.db_deletion_protection

  depends_on = [google_service_networking_connection.private_vpc]

  settings {
    tier              = var.db_tier
    availability_type = var.db_availability_type
    disk_size         = var.db_disk_size
    disk_autoresize   = true

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.vpc.id
      ssl_mode        = "ENCRYPTED_ONLY" # GCP-0015 — require TLS for connections
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "02:00"
    }

    dynamic "database_flags" {
      for_each = {
        log_connections    = "on"
        log_disconnections = "on"
        log_checkpoints    = "on"
        log_lock_waits     = "on"
      }
      content {
        name  = database_flags.key
        value = database_flags.value
      }
    }
  }
}

resource "google_sql_database" "db" {
  name     = var.db_name
  instance = google_sql_database_instance.pg.name
}

resource "google_sql_user" "user" {
  name     = var.db_username
  instance = google_sql_database_instance.pg.name
  password = random_password.db.result
}

# ----------------------------------------------------------------------------
# Secret Manager — platform secret (consumed by the chart / synced via the
# Secret Manager CSI driver in production; this module stores the source value).
# ----------------------------------------------------------------------------
resource "google_secret_manager_secret" "platform" {
  secret_id = "${var.name}-platform"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "platform" {
  secret = google_secret_manager_secret.platform.id
  secret_data = jsonencode({
    DATABASE_URL             = local.database_url
    ENCRYPTION_KEY           = local.encryption_key
    ROUTER_RS256_PRIVATE_KEY = local.router_rs256_private_key
    NEXTAUTH_SECRET          = local.nextauth_secret
  })
}

# ----------------------------------------------------------------------------
# Optional Google-managed certificate for the GCE ingress.
# ----------------------------------------------------------------------------
resource "google_compute_managed_ssl_certificate" "mcpfinder" {
  count = length(var.managed_certificate_domains) > 0 ? 1 : 0
  name  = "${var.name}-cert"

  managed {
    domains = var.managed_certificate_domains
  }
}

# ----------------------------------------------------------------------------
# Sealfleet Helm release (external Cloud SQL; GCE ingress).
# ----------------------------------------------------------------------------
resource "helm_release" "mcpfinder" {
  count = var.install_helm_release ? 1 : 0

  name             = var.release_name
  namespace        = var.release_namespace
  create_namespace = true
  chart            = local.chart_path

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
      className     = "gce"
      tlsSecretName = ""
      annotations = length(var.managed_certificate_domains) > 0 ? {
        "networking.gke.io/managed-certificates" = google_compute_managed_ssl_certificate.mcpfinder[0].name
        "kubernetes.io/ingress.allow-http"       = "false"
      } : {}
      hosts = {
        portal   = var.ingress_hosts.portal
        router   = var.ingress_hosts.router
        deploy   = var.ingress_hosts.deploy
        registry = var.ingress_hosts.registry
      }
      tls = {
        # GCE ingress + Google-managed cert terminates TLS; no in-cluster Secret.
        enabled     = false
        certManager = { enabled = false }
      }
    }
  })]

  depends_on = [
    google_container_node_pool.primary,
    google_sql_database.db,
    google_sql_user.user,
  ]
}

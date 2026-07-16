output "project_id" {
  description = "GCP project ID."
  value       = var.project_id
}

output "region" {
  description = "GCP region."
  value       = var.region
}

output "cluster_name" {
  description = "GKE cluster name."
  value       = google_container_cluster.gke.name
}

output "cluster_endpoint" {
  description = "GKE API server endpoint."
  value       = google_container_cluster.gke.endpoint
  sensitive   = true
}

output "kubeconfig_command" {
  description = "Command to configure kubectl for the cluster."
  value       = "gcloud container clusters get-credentials ${google_container_cluster.gke.name} --region ${var.region} --project ${var.project_id}"
}

output "cloudsql_instance" {
  description = "Cloud SQL instance connection name."
  value       = google_sql_database_instance.pg.connection_name
}

output "cloudsql_private_ip" {
  description = "Cloud SQL private IP."
  value       = google_sql_database_instance.pg.private_ip_address
}

output "platform_secret_id" {
  description = "Secret Manager secret ID holding the platform secret."
  value       = google_secret_manager_secret.platform.secret_id
}

output "ingress_hosts" {
  description = "Public hostnames per service (point DNS at the provisioned ingress IP)."
  value       = var.ingress_hosts
}

output "database_url" {
  description = "Computed DATABASE_URL for the platform (sensitive)."
  value       = local.database_url
  sensitive   = true
}

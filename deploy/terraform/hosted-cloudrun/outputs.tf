output "portal_url" {
  description = "Cloud Run URL of the portal (set portal_public_url to a custom domain for real auth)."
  value       = google_cloud_run_v2_service.portal.uri
}

output "router_url" {
  description = "Cloud Run URL of the runtime router / API."
  value       = google_cloud_run_v2_service.router.uri
}

output "registry_url" {
  description = "Cloud Run URL of the registry."
  value       = google_cloud_run_v2_service.registry.uri
}

output "next_steps" {
  description = "What to do after apply."
  value       = "1) Run DB migrations against database_url (see README). 2) If portal_public_url was empty, set it to the portal_url above (or a custom domain) and re-apply so NEXTAUTH_URL is correct."
}

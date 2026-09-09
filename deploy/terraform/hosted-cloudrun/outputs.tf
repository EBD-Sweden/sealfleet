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

output "agent_url" {
  description = "Cloud Run URL of the chat agent (called by the portal; gated by RUNTIME_API_KEY)."
  value       = google_cloud_run_v2_service.agent.uri
}

output "llm_proxy_url" {
  description = "Cloud Run URL of the llm-proxy (token-gated)."
  value       = google_cloud_run_v2_service.llm_proxy.uri
}

output "runtime_api_key" {
  description = "Operator key shared by portal, agent and llm-proxy. Register it in api_keys (see README) so the router accepts it."
  value       = local.runtime_api_key
  sensitive   = true
}

output "next_steps" {
  description = "What to do after apply."
  value       = "1) Run DB migrations against database_url (see README). 2) Register the runtime_api_key output in api_keys with allow_identity_delegation (README). 3) If portal_public_url was empty, set it to the portal_url above (or a custom domain) and re-apply so NEXTAUTH_URL is correct."
}

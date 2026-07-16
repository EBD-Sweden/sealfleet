output "region" {
  description = "AWS region."
  value       = var.region
}

output "cluster_name" {
  description = "EKS cluster name."
  value       = module.eks.cluster_name
}

output "cluster_endpoint" {
  description = "EKS API server endpoint."
  value       = module.eks.cluster_endpoint
}

output "kubeconfig_command" {
  description = "Command to configure kubectl for the cluster."
  value       = "aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.region}"
}

output "rds_endpoint" {
  description = "RDS Postgres endpoint (host:port)."
  value       = module.rds.db_instance_endpoint
}

output "rds_database_name" {
  description = "Initial database name."
  value       = var.db_name
}

output "platform_secret_arn" {
  description = "Secrets Manager ARN holding the platform secret (DATABASE_URL, ENCRYPTION_KEY, ROUTER_RS256_PRIVATE_KEY, NEXTAUTH_SECRET)."
  value       = aws_secretsmanager_secret.platform.arn
}

output "ingress_hosts" {
  description = "Public hostnames per service (point DNS at the provisioned ALB)."
  value       = var.ingress_hosts
}

output "database_url" {
  description = "Computed DATABASE_URL for the platform (sensitive)."
  value       = local.database_url
  sensitive   = true
}

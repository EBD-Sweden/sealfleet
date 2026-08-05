output "portal_url" {
  description = "Portal service URL (App Runner default). Use portal_domain for a branded URL."
  value       = "https://${aws_apprunner_service.portal.service_url}"
}

output "router_url" {
  value = "https://${aws_apprunner_service.router.service_url}"
}

output "registry_url" {
  value = "https://${aws_apprunner_service.registry.service_url}"
}

output "ecr_repositories" {
  description = "ECR repo URLs to push the sealfleet-* images into (see README)."
  value       = { for k, r in aws_ecr_repository.svc : k => r.repository_url }
}

output "instance_role_arn" {
  description = "App Runner instance role — granted Secrets Manager read + aws-marketplace ResolveCustomer/GetEntitlements."
  value       = aws_iam_role.instance.arn
}

output "aws_marketplace_fulfillment_url" {
  description = "Set this as the SaaS product's Fulfillment URL in the AWS Marketplace Management Portal."
  value       = "${var.portal_domain != "" ? "https://${var.portal_domain}" : "https://${aws_apprunner_service.portal.service_url}"}/api/aws-marketplace"
}

output "aws_marketplace_notification_url" {
  description = "Subscribe the product's SNS topic to this endpoint (subscription/entitlement notifications)."
  value       = "${var.portal_domain != "" ? "https://${var.portal_domain}" : "https://${aws_apprunner_service.portal.service_url}"}/api/aws-marketplace/notification"
}

output "portal_custom_domain_dns" {
  description = "DNS validation + target records for the portal custom domain (empty if portal_domain unset)."
  value = var.portal_domain != "" ? {
    target          = aws_apprunner_custom_domain_association.portal[0].dns_target
    validation_records = aws_apprunner_custom_domain_association.portal[0].certificate_validation_records
  } : null
}

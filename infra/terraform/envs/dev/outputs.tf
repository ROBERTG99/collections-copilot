output "alb_dns_name" {
  description = "Point the console hostname (CNAME or alias) here."
  value       = module.alb.dns_name
}

output "ecr_repository_urls" {
  description = "Image repositories by service."
  value       = module.ecr.repository_urls
}

output "cognito_issuer_url" {
  description = "OIDC issuer for CC_OIDC_ISSUER."
  value       = module.cognito.issuer_url
}

output "rds_endpoint" {
  description = "Database endpoint."
  value       = module.rds.endpoint
}

output "sqs_queue_urls" {
  description = "Work queue URLs."
  value       = module.sqs.queue_urls
}

output "uploads_bucket_name" {
  description = "Uploads bucket."
  value       = module.s3.bucket_name
}

output "github_plan_role_arn" {
  description = "Set as vars.AWS_PLAN_ROLE_ARN to enable the plan job in terraform.yml."
  value       = module.github_oidc.role_arn
}

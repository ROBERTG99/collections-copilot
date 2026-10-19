output "repository_urls" {
  description = "Repository URLs by name."
  value       = { for k, r in aws_ecr_repository.this : k => r.repository_url }
}

output "repository_arns" {
  description = "Repository ARNs by name."
  value       = { for k, r in aws_ecr_repository.this : k => r.arn }
}

output "posture" {
  description = "Tag mutability and scanning per repository (for the tests)."
  value = {
    for k, r in aws_ecr_repository.this : k => {
      immutable    = r.image_tag_mutability == "IMMUTABLE"
      scan_on_push = r.image_scanning_configuration[0].scan_on_push
      encryption   = r.encryption_configuration[0].encryption_type
    }
  }
}

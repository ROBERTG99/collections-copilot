output "secret_arns" {
  description = "Secret ARNs by name."
  value       = { for k, s in aws_secretsmanager_secret.this : k => s.arn }
}

output "encryption" {
  description = "KMS key of every secret (for the encryption tests)."
  value       = { for k, s in aws_secretsmanager_secret.this : k => s.kms_key_id }
}

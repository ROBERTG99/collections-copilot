output "data_key_arn" {
  description = "ARN of the symmetric data key."
  value       = aws_kms_key.data.arn
}

output "signing_key_arn" {
  description = "ARN of the RSA_2048 signing key."
  value       = aws_kms_key.signing.arn
}

output "data_key_rotation" {
  description = "Whether automatic rotation is on for the data key."
  value       = aws_kms_key.data.enable_key_rotation
}

output "signing_key_spec" {
  description = "Key spec and usage of the signing key."
  value       = { spec = aws_kms_key.signing.customer_master_key_spec, usage = aws_kms_key.signing.key_usage }
}

output "key_policies" {
  description = "Key policies (resource policies; for the IAM tests)."
  value       = { data = local.data_key_policy, signing = local.signing_key_policy }
}

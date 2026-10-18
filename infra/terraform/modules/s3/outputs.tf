output "bucket_arn" {
  description = "Bucket ARN."
  value       = aws_s3_bucket.this.arn
}

output "bucket_name" {
  description = "Bucket name."
  value       = aws_s3_bucket.this.bucket
}

output "encryption" {
  description = "Default encryption (for the encryption tests)."
  value = {
    algorithm = one(aws_s3_bucket_server_side_encryption_configuration.this.rule).apply_server_side_encryption_by_default[0].sse_algorithm
    key       = one(aws_s3_bucket_server_side_encryption_configuration.this.rule).apply_server_side_encryption_by_default[0].kms_master_key_id
  }
}

output "public_access_blocked" {
  description = "All four public-access-block settings are on."
  value = alltrue([
    aws_s3_bucket_public_access_block.this.block_public_acls,
    aws_s3_bucket_public_access_block.this.block_public_policy,
    aws_s3_bucket_public_access_block.this.ignore_public_acls,
    aws_s3_bucket_public_access_block.this.restrict_public_buckets,
  ])
}

output "versioning" {
  description = "Versioning status."
  value       = aws_s3_bucket_versioning.this.versioning_configuration[0].status
}

output "policy_documents" {
  description = "Bucket policy (for the IAM tests)."
  value       = { uploads_bucket = local.bucket_policy }
}

output "expire_days" {
  description = "Days before an upload expires (for the tests)."
  value       = one(aws_s3_bucket_lifecycle_configuration.this.rule[0].expiration[*].days)
}

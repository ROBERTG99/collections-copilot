output "endpoint" {
  description = "Database endpoint (host:port)."
  value       = aws_db_instance.this.endpoint
}

output "master_secret_arn" {
  description = "Secrets Manager ARN of the RDS-managed master password."
  value       = one(aws_db_instance.this.master_user_secret[*].secret_arn)
}

output "security_group_id" {
  description = "Database security group."
  value       = aws_security_group.this.id
}

output "subnet_ids" {
  description = "Subnets of the DB subnet group (for the network tests)."
  value       = aws_db_subnet_group.this.subnet_ids
}

output "posture" {
  description = "Security-relevant settings (for the tests)."
  value = {
    publicly_accessible = aws_db_instance.this.publicly_accessible
    storage_encrypted   = aws_db_instance.this.storage_encrypted
    kms_key_id          = aws_db_instance.this.kms_key_id
    backup_retention    = aws_db_instance.this.backup_retention_period
    deletion_protection = aws_db_instance.this.deletion_protection
    managed_password    = aws_db_instance.this.manage_master_user_password
  }
}

output "managed_policy_arns" {
  description = "AWS-managed policies attached (documented exceptions)."
  value       = [aws_iam_role_policy_attachment.monitoring.policy_arn]
}

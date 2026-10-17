# Two customer-managed keys: a symmetric data key (RDS, S3, SQS, logs, Secrets Manager, SNS, ECR)
# and an RSA_2048 signing key the worker uses to sign agent JWTs for mcp-gateway.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_root = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
  data_key_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AccountAdministers"
        Effect    = "Allow"
        Principal = { AWS = local.account_root }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "CloudWatchLogsEncrypt"
        Effect    = "Allow"
        Principal = { Service = "logs.${data.aws_region.current.region}.amazonaws.com" }
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
        Resource  = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:*"
          }
        }
      },
      {
        Sid       = "AwsServicesInAccount"
        Effect    = "Allow"
        Principal = { Service = ["sns.amazonaws.com", "cloudwatch.amazonaws.com", "events.amazonaws.com"] }
        Action    = ["kms:Decrypt", "kms:GenerateDataKey*"]
        Resource  = "*"
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        }
      },
    ]
  })
  # Key policies are resource policies: "kms:*" for the account root and "Resource = *" (meaning
  # this key) are the documented exception in envs/dev/tests/iam.tftest.hcl.
  signing_key_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AccountAdministers"
      Effect    = "Allow"
      Principal = { AWS = local.account_root }
      Action    = "kms:*"
      Resource  = "*"
    }]
  })
}

resource "aws_kms_key" "data" {
  description             = "${var.project}-${var.environment} data key"
  deletion_window_in_days = var.deletion_window_days
  enable_key_rotation     = true
  policy                  = local.data_key_policy
}

resource "aws_kms_alias" "data" {
  name          = "alias/${var.project}-${var.environment}-data"
  target_key_id = aws_kms_key.data.key_id
}

resource "aws_kms_key" "signing" {
  description              = "${var.project}-${var.environment} agent JWT signing key"
  deletion_window_in_days  = var.deletion_window_days
  key_usage                = "SIGN_VERIFY"
  customer_master_key_spec = "RSA_2048"
  policy                   = local.signing_key_policy
}

resource "aws_kms_alias" "signing" {
  name          = "alias/${var.project}-${var.environment}-signing"
  target_key_id = aws_kms_key.signing.key_id
}

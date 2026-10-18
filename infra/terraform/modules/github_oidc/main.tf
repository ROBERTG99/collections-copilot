# Plan-only role for GitHub Actions (terraform.yml, `main` only): read-only AWS access plus the
# state bucket when one is configured. It can never apply, and an explicit deny keeps it from
# reading data (log events, database logs, users, objects, messages, image layers): a plan needs
# configuration only.

locals {
  provider_arn = var.oidc_provider_arn != null ? var.oidc_provider_arn : aws_iam_openid_connect_provider.github[0].arn
  deny_data = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "NoDataReads"
        Effect = "Deny"
        Action = [
          "logs:GetLogEvents", "logs:FilterLogEvents", "logs:StartQuery", "logs:GetQueryResults",
          "logs:StartLiveTail", "rds:DownloadDBLogFilePortion", "rds:DownloadCompleteDBLogFile",
          "cognito-idp:ListUsers", "cognito-idp:ListUsersInGroup", "cognito-idp:AdminGetUser",
          "sqs:ReceiveMessage", "secretsmanager:GetSecretValue", "kms:Decrypt",
          "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "dynamodb:GetItem", "dynamodb:Query",
          "dynamodb:Scan", "logs:GetLogRecord", "lambda:GetFunction", "xray:BatchGetTraces",
          "xray:GetTraceSummaries", "ssm:GetParameter", "ssm:GetParameters",
          "ssm:GetParametersByPath", "athena:GetQueryResults", "cognito-idp:AdminListGroupsForUser",
        ]
        Resource = ["*"]
      },
      {
        Sid         = "NoObjectReadsOutsideState"
        Effect      = "Deny"
        Action      = ["s3:GetObject", "s3:GetObjectVersion"]
        NotResource = var.state_bucket_arn == null ? ["arn:aws:s3:::none-configured/*"] : ["${var.state_bucket_arn}/*"]
      },
    ]
  })
  trust = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = local.provider_arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repository}:ref:refs/heads/main"
        }
      }
    }]
  })
  state_policy = var.state_bucket_arn == null ? null : jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadState"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${var.state_bucket_arn}/*"]
      },
      {
        Sid      = "ListState"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [var.state_bucket_arn]
      },
    ]
  })
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.oidc_provider_arn == null ? 1 : 0 # an account has at most one per URL
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

resource "aws_iam_role" "plan" {
  name_prefix          = "${var.name}-gh-plan-"
  assume_role_policy   = local.trust
  max_session_duration = 3600
}

# AWS's managed ReadOnlyAccess: the documented exception in envs/dev/tests/iam.tftest.hcl
# (a plan reads every resource type the configuration manages).
resource "aws_iam_role_policy_attachment" "read_only" {
  role       = aws_iam_role.plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

resource "aws_iam_role_policy" "state" {
  count  = var.state_bucket_arn == null ? 0 : 1
  role   = aws_iam_role.plan.id
  policy = local.state_policy
}

resource "aws_iam_role_policy" "deny_data" {
  role   = aws_iam_role.plan.id
  policy = local.deny_data
}

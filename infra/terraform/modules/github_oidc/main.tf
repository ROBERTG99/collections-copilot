# Plan-only role for GitHub Actions (terraform.yml, `main` only): read-only AWS access plus the
# state bucket when one is configured. It can never apply.

locals {
  trust = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
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

# User pool for the console: authorization code with PKCE for the BFF (a public client: the BFF
# holds no client secret, PKCE binds the code to the session),
# groups analyst, supervisor and admin (the API's roles), and an immutable custom:tenant_id that a
# Pre Token Generation (V2) trigger copies into access tokens (Essentials tier), since the API
# reads access tokens only.

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

resource "aws_cognito_user_pool" "this" {
  name                = var.name
  user_pool_tier      = "ESSENTIALS" # access-token customization needs Essentials or Plus
  deletion_protection = "ACTIVE"
  mfa_configuration   = var.mfa
  lambda_config {
    pre_token_generation_config {
      lambda_arn     = aws_lambda_function.pre_token.arn
      lambda_version = "V2_0"
    }
  }
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  password_policy {
    minimum_length                   = 14
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 3
  }
  software_token_mfa_configuration {
    enabled = true
  }
  admin_create_user_config {
    allow_admin_create_user_only = true # no self sign-up
  }
  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
  schema {
    name                     = "tenant_id"
    attribute_data_type      = "String"
    mutable                  = false
    developer_only_attribute = false
    required                 = false
    string_attribute_constraints {
      min_length = 36
      max_length = 36
    }
  }
}

resource "aws_cognito_user_pool_domain" "this" {
  domain       = var.domain_prefix
  user_pool_id = aws_cognito_user_pool.this.id
}

resource "aws_cognito_user_pool_client" "web" {
  name                                 = "${var.name}-web"
  user_pool_id                         = aws_cognito_user_pool.this.id
  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = var.callback_urls
  logout_urls                          = var.logout_urls
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true
  explicit_auth_flows                  = ["ALLOW_REFRESH_TOKEN_AUTH"]
  access_token_validity                = 15
  id_token_validity                    = 15
  refresh_token_validity               = 12
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "hours"
  }
  read_attributes  = ["email", "custom:tenant_id"]
  write_attributes = ["email"]
}

resource "aws_cognito_user_group" "this" {
  for_each     = toset(["analyst", "supervisor", "admin"])
  name         = each.key
  user_pool_id = aws_cognito_user_pool.this.id
  description  = "collections-copilot ${each.key}"
}

# --- Pre Token Generation trigger -------------------------------------------------------------

data "archive_file" "pre_token" {
  type        = "zip"
  source_file = "${path.module}/lambda/pre_token.py"
  output_path = "${path.module}/.build/pre_token.zip"
}

resource "aws_iam_role" "pre_token" {
  name_prefix = "${var.name}-pretoken-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id } }
    }]
  })
}

resource "aws_cloudwatch_log_group" "pre_token" {
  #checkov:skip=CKV_AWS_338:§16.3 sets 30-day retention for this environment
  name              = "/aws/lambda/${var.name}-pre-token"
  retention_in_days = 30
  kms_key_id        = var.kms_key_arn
}

resource "aws_iam_role_policy" "pre_token" {
  role = aws_iam_role.pre_token.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "WriteOwnLogs"
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = ["${aws_cloudwatch_log_group.pre_token.arn}:log-stream:*"]
    }]
  })
}

resource "aws_lambda_function" "pre_token" {
  #checkov:skip=CKV_AWS_116:Cognito invokes the trigger synchronously; a dead-letter queue never receives these events
  #checkov:skip=CKV_AWS_117:the function calls no network service, so it runs outside the VPC
  #checkov:skip=CKV_AWS_272:one file deployed from this repository by Terraform; code signing is not set up for dev
  #checkov:skip=CKV_AWS_50:X-Ray tracing is not used in this environment (traces go through OpenTelemetry)
  function_name                  = "${var.name}-pre-token"
  role                           = aws_iam_role.pre_token.arn
  runtime                        = "python3.13"
  handler                        = "pre_token.handler"
  filename                       = data.archive_file.pre_token.output_path
  source_code_hash               = data.archive_file.pre_token.output_base64sha256
  timeout                        = 5
  memory_size                    = 128
  reserved_concurrent_executions = var.pre_token_concurrency
  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.pre_token.name
  }
  depends_on = [aws_iam_role_policy.pre_token]
}

resource "aws_lambda_permission" "cognito" {
  statement_id   = "CognitoPreTokenGeneration"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.pre_token.function_name
  principal      = "cognito-idp.amazonaws.com"
  source_arn     = aws_cognito_user_pool.this.arn
  source_account = data.aws_caller_identity.current.account_id
}

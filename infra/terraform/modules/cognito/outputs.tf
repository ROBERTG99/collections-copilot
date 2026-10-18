output "issuer_url" {
  description = "OIDC issuer URL (CC_OIDC_ISSUER)."
  value       = "https://cognito-idp.${data.aws_region.current.region}.amazonaws.com/${aws_cognito_user_pool.this.id}"
}

output "hosted_ui_url" {
  description = "Hosted UI base URL (authorize, token and logout endpoints)."
  value       = "https://${aws_cognito_user_pool_domain.this.domain}.auth.${data.aws_region.current.region}.amazoncognito.com"
}

output "client_id" {
  description = "Web (BFF) client id."
  value       = aws_cognito_user_pool_client.web.id
}

output "groups" {
  description = "Group names."
  value       = sort(keys(aws_cognito_user_group.this))
}

output "client_flows" {
  description = "OAuth flows of the web client (for the tests)."
  value = {
    flows           = aws_cognito_user_pool_client.web.allowed_oauth_flows
    generate_secret = aws_cognito_user_pool_client.web.generate_secret
    callback_urls   = aws_cognito_user_pool_client.web.callback_urls
  }
}

output "tenant_attribute" {
  description = "The custom:tenant_id schema (for the tests)."
  value = [for s in aws_cognito_user_pool.this.schema : {
    name    = s.name
    mutable = s.mutable
  } if s.name == "tenant_id"]
}

output "pre_token_trigger" {
  description = "How access tokens get custom:tenant_id (for the tests)."
  value = {
    user_pool_tier   = aws_cognito_user_pool.this.user_pool_tier
    lambda_version   = one(aws_cognito_user_pool.this.lambda_config[0].pre_token_generation_config[*].lambda_version)
    lambda_arn       = aws_lambda_function.pre_token.arn
    log_kms_key_id   = aws_cloudwatch_log_group.pre_token.kms_key_id
    log_retention    = aws_cloudwatch_log_group.pre_token.retention_in_days
    invoke_principal = aws_lambda_permission.cognito.principal
  }
}

output "mfa" {
  description = "MFA requirement of the user pool."
  value       = aws_cognito_user_pool.this.mfa_configuration
}

output "log_groups" {
  description = "Log groups created by this module, with key and retention (for the tests)."
  value = {
    (aws_cloudwatch_log_group.pre_token.name) = {
      kms_key_id        = aws_cloudwatch_log_group.pre_token.kms_key_id
      retention_in_days = aws_cloudwatch_log_group.pre_token.retention_in_days
    }
  }
}

output "policy_documents" {
  description = "Inline identity policies created here (for the IAM tests)."
  value       = { cognito_pre_token = aws_iam_role_policy.pre_token.policy }
}

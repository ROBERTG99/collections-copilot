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

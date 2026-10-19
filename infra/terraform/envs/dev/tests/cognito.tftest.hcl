# Identity (T-46): code flow with PKCE for the BFF (a public client), the API's three roles as
# groups, and an immutable tenant attribute.

mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_region" {
    defaults = { region = "us-east-1", name = "us-east-1" }
  }
  mock_data "aws_availability_zones" {
    defaults = { names = ["us-east-1a", "us-east-1b", "us-east-1c"] }
  }
  # Well-formed ARNs and ids for computed attributes the provider validates downstream.
  mock_resource "aws_kms_key" {
    defaults = { arn = "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-0000-0000-000000000001", key_id = "00000000-0000-0000-0000-000000000001" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/mock" }
  }
  mock_resource "aws_lb" {
    defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/mock/0123456789abcdef", arn_suffix = "app/mock/0123456789abcdef", dns_name = "mock.us-east-1.elb.amazonaws.com" }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/mock/0123456789abcdef" }
  }
  mock_resource "aws_lb_listener" {
    defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:listener/app/mock/0123456789abcdef/0123456789abcdef" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:us-east-1:123456789012:log-group:mock" }
  }
  mock_resource "aws_sns_topic" {
    defaults = { arn = "arn:aws:sns:us-east-1:123456789012:mock" }
  }
  mock_resource "aws_cognito_user_pool" {
    defaults = { id = "us-east-1_Mock12345", arn = "arn:aws:cognito-idp:us-east-1:123456789012:userpool/us-east-1_Mock12345" }
  }
  mock_resource "aws_ecs_cluster" {
    defaults = { arn = "arn:aws:ecs:us-east-1:123456789012:cluster/mock" }
  }
  mock_resource "aws_ecs_task_definition" {
    defaults = { arn = "arn:aws:ecs:us-east-1:123456789012:task-definition/mock:1" }
  }
  mock_resource "aws_service_discovery_http_namespace" {
    defaults = { arn = "arn:aws:servicediscovery:us-east-1:123456789012:namespace/ns-mock" }
  }
  mock_resource "aws_sqs_queue" {
    defaults = { arn = "arn:aws:sqs:us-east-1:123456789012:mock", url = "https://sqs.us-east-1.amazonaws.com/123456789012/mock" }
  }
  mock_resource "aws_secretsmanager_secret" {
    defaults = { arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:mock-AbCdEf" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::mock-bucket" }
  }
  mock_resource "aws_iam_openid_connect_provider" {
    defaults = { arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" }
  }
  mock_resource "aws_ecr_repository" {
    defaults = { arn = "arn:aws:ecr:us-east-1:123456789012:repository/mock", repository_url = "123456789012.dkr.ecr.us-east-1.amazonaws.com/mock" }
  }
  mock_resource "aws_wafv2_web_acl" {
    defaults = { arn = "arn:aws:wafv2:us-east-1:123456789012:regional/webacl/mock/00000000-0000-0000-0000-000000000003" }
  }
  mock_resource "aws_vpc_endpoint" {
    defaults = { prefix_list_id = "pl-63a5400a" }
  }
  mock_resource "aws_lambda_function" {
    defaults = { arn = "arn:aws:lambda:us-east-1:123456789012:function:mock" }
  }
  mock_resource "aws_db_instance" {
    defaults = { arn = "arn:aws:rds:us-east-1:123456789012:db:mock", endpoint = "mock.abc.us-east-1.rds.amazonaws.com:5432" }
  }
}

# The signing key must be told apart from the data key.
override_resource {
  target = module.kms.aws_kms_key.signing
  values = {
    arn    = "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-0000-0000-000000000002"
    key_id = "00000000-0000-0000-0000-000000000002"
  }
}

variables {
  image_tag          = "0.1.0"
  certificate_arn    = "arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-0000-0000-000000000000"
  alarm_email        = "alerts@example.com"
  smtp_host          = "email-smtp.example.com"
  twilio_from_number = "+15550001234"
}

run "console_identity" {
  command = apply

  assert {
    condition     = toset(module.cognito.groups) == toset(["admin", "analyst", "supervisor"])
    error_message = "Groups must match the API roles analyst, supervisor and admin."
  }
  assert {
    condition     = toset(module.cognito.client_flows.flows) == toset(["code"]) && !module.cognito.client_flows.generate_secret
    error_message = "The web client uses only the authorization code flow, as a public client (no secret). Cognito does not enforce PKCE; the BFF always sends S256 (web/tests/unit/oidc.test.ts)."
  }
  assert {
    condition     = module.cognito.pre_token_trigger.user_pool_tier == "ESSENTIALS" && module.cognito.pre_token_trigger.lambda_version == "V2_0"
    error_message = "A V2 Pre Token Generation trigger (Essentials tier) must put custom:tenant_id into access tokens, which the API reads."
  }
  assert {
    condition     = module.cognito.pre_token_trigger.invoke_principal == "cognito-idp.amazonaws.com"
    error_message = "Only Cognito may invoke the trigger."
  }
  assert {
    condition     = module.cognito.mfa == "ON"
    error_message = "MFA is required: supervisors and admins approve sends."
  }
  assert {
    condition     = alltrue([for u in module.cognito.client_flows.callback_urls : startswith(u, "https://")])
    error_message = "Callbacks must be HTTPS."
  }
  assert {
    condition     = length(module.cognito.tenant_attribute) == 1 && module.cognito.tenant_attribute[0].mutable == false
    error_message = "custom:tenant_id must exist and be immutable."
  }
  assert {
    condition     = startswith(module.cognito.issuer_url, "https://cognito-idp.us-east-1.amazonaws.com/")
    error_message = "The issuer URL is the regional Cognito endpoint."
  }
}

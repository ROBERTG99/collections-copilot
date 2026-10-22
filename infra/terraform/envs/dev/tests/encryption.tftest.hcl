# Encryption at rest with the customer-managed data key.

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

run "everything_is_encrypted_with_the_data_key" {
  command = apply

  assert {
    condition     = module.rds.posture.storage_encrypted && module.rds.posture.kms_key_id == module.kms.data_key_arn
    error_message = "RDS storage must be encrypted with the data key."
  }
  assert {
    condition     = module.rds.posture.managed_password
    error_message = "The master password must be managed by RDS in Secrets Manager."
  }
  assert {
    condition     = module.s3.encryption.algorithm == "aws:kms" && module.s3.encryption.key == module.kms.data_key_arn
    error_message = "The uploads bucket must use SSE-KMS with the data key."
  }
  assert {
    condition     = module.s3.public_access_blocked && module.s3.versioning == "Enabled"
    error_message = "The uploads bucket must block public access and keep versions."
  }
  assert {
    condition     = module.s3.expire_days == 30
    error_message = "Uploads (debtor data) expire after 30 days."
  }
  assert {
    condition     = length(module.sqs.encryption) == 4 && alltrue([for q, key in module.sqs.encryption : key == module.kms.data_key_arn])
    error_message = "Every queue and dead-letter queue must use the data key."
  }
  assert {
    condition     = alltrue([for q, n in module.sqs.redrive : n == 5])
    error_message = "Messages move to the DLQ after 5 receives."
  }
  assert {
    condition     = alltrue([for s, key in module.secrets.encryption : key == module.kms.data_key_arn])
    error_message = "Every secret must use the data key."
  }
  assert {
    condition     = alltrue([for g, v in module.observability.log_groups : v.kms == module.kms.data_key_arn]) && module.network.flow_log_group.kms == module.kms.data_key_arn && module.alb.waf_log_group.kms == module.kms.data_key_arn
    error_message = "Every log group must be encrypted with the data key."
  }
  assert {
    condition = alltrue([for t in module.rds.log_exports : contains(keys(module.rds.log_groups), "/aws/rds/instance/collections-copilot-dev/${t}")]) && alltrue([
      for g, v in merge(module.rds.log_groups, module.cognito.log_groups) : v.kms_key_id == module.kms.data_key_arn && v.retention_in_days == 30
    ])
    error_message = "Log groups AWS services write to (RDS exports, the Cognito trigger) must exist up front, with the data key and 30 days."
  }
  assert {
    condition     = output.container_insights_log_group.kms_key_id == module.kms.data_key_arn && output.container_insights_log_group.retention_in_days == 30
    error_message = "The Container Insights log group must be created with the data key and 30 days."
  }
  assert {
    condition     = module.rds.parameters["log_parameter_max_length"] == "0" && module.rds.parameters["log_parameter_max_length_on_error"] == "0" && module.rds.parameters["rds.force_ssl"] == "1"
    error_message = "Database logs never carry bind values (debtor data), and TLS is required."
  }
  assert {
    condition     = module.observability.topic_kms == module.kms.data_key_arn
    error_message = "The alarm topic must be encrypted."
  }
  assert {
    condition     = alltrue([for r, p in module.ecr.posture : p.encryption == "KMS" && p.immutable && p.scan_on_push])
    error_message = "Every repository must be KMS-encrypted, immutable and scanned on push."
  }
  assert {
    condition     = module.kms.data_key_rotation
    error_message = "The data key must rotate."
  }
  assert {
    condition     = module.kms.signing_key_spec.spec == "RSA_2048" && module.kms.signing_key_spec.usage == "SIGN_VERIFY"
    error_message = "The signing key must be RSA_2048 for signing."
  }
}

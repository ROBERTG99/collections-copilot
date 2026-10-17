# Network placement (T-45). `command = apply` runs against the mock provider: nothing is created.

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
  image_tag       = "0.1.0"
  certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-0000-0000-000000000000"
  alarm_email     = "alerts@example.com"
}

run "database_and_tasks_stay_private" {
  command = apply

  assert {
    condition     = toset(module.rds.subnet_ids) == toset(module.network.private_subnet_ids)
    error_message = "The database subnet group must use exactly the private subnets."
  }
  assert {
    condition     = length(setintersection(toset(module.rds.subnet_ids), toset(module.network.public_subnet_ids))) == 0
    error_message = "The database must never sit in a public subnet."
  }
  assert {
    condition     = module.rds.posture.publicly_accessible == false
    error_message = "The database must not be publicly accessible."
  }
  assert {
    condition = alltrue([
      for name, svc in module.service :
      svc.posture.assign_public_ip == false && toset(svc.posture.subnets) == toset(module.network.private_subnet_ids)
    ])
    error_message = "Every ECS service must run in the private subnets without a public IP."
  }
  assert {
    condition     = module.network.subnets_map_public_ip == false
    error_message = "No subnet may assign public IPs on launch."
  }
  assert {
    condition     = length(module.network.private_subnet_ids) == 2 && length(module.network.public_subnet_ids) == 2
    error_message = "The VPC spans two availability zones."
  }
  assert {
    condition     = module.network.nat_gateway_count == 1
    error_message = "dev uses a single NAT gateway."
  }
  assert {
    condition = length(setsubtract(
      toset(["bedrock-runtime", "sqs", "secretsmanager", "ecr.api", "ecr.dkr", "logs", "kms"]),
      toset(module.network.interface_endpoints),
    )) == 0
    error_message = "Bedrock runtime, SQS, Secrets Manager, ECR, logs and KMS need interface endpoints."
  }
}

run "only_webhooks_reach_the_api_publicly" {
  command = apply

  assert {
    condition     = keys(module.alb.routes) == ["api"] && toset(module.alb.routes["api"]) == toset(["/webhooks/*"])
    error_message = "The ALB may route only /webhooks/* to the API; everything else goes to the web BFF."
  }
  assert {
    condition     = module.alb.listeners.http_redirect == "redirect"
    error_message = "Port 80 must only redirect to HTTPS."
  }
  assert {
    condition     = startswith(module.alb.listeners.ssl_policy, "ELBSecurityPolicy-TLS13")
    error_message = "The HTTPS listener must use a TLS 1.3 policy."
  }
  assert {
    condition     = module.alb.listeners.idle_timeout == 120
    error_message = "The idle timeout must be 120 s for SSE progress streams."
  }
}

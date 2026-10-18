# Invalid inputs are rejected before anything is planned (T-46).

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

run "valid_inputs_plan" {
  command = plan
}

run "bedrock_ids_must_be_anthropic" {
  command = plan
  variables {
    bedrock_model_ids = ["meta.llama3-70b-instruct-v1:0"]
  }
  expect_failures = [var.bedrock_model_ids]
}

run "latest_tag_is_refused" {
  command = plan
  variables {
    image_tag = "latest"
  }
  expect_failures = [var.image_tag]
}

run "certificate_must_be_acm" {
  command = plan
  variables {
    certificate_arn = "not-an-arn"
  }
  expect_failures = [var.certificate_arn]
}

run "worker_max_tasks_bounded" {
  command = plan
  variables {
    worker_max_tasks = 0
  }
  expect_failures = [var.worker_max_tasks]
}

run "budget_must_be_positive" {
  command = plan
  variables {
    monthly_budget_usd = 0
  }
  expect_failures = [var.monthly_budget_usd]
}

run "alarm_email_must_be_an_address" {
  command = plan
  variables {
    alarm_email = "ops"
  }
  expect_failures = [var.alarm_email]
}

run "rds_keeps_at_least_seven_days_of_backups" {
  command = plan
  module {
    source = "../../modules/rds"
  }
  variables {
    name                       = "cc-test"
    vpc_id                     = "vpc-123"
    subnet_ids                 = ["subnet-a", "subnet-b"]
    allowed_security_group_ids = {}
    kms_key_arn                = "arn:aws:kms:us-east-1:123456789012:key/test"
    backup_retention_days      = 3
  }
  expect_failures = [var.backup_retention_days]
}

run "kms_deletion_window_bounded" {
  command = plan
  module {
    source = "../../modules/kms"
  }
  variables {
    project              = "cc"
    environment          = "dev"
    deletion_window_days = 3
  }
  expect_failures = [var.deletion_window_days]
}

run "region_must_be_an_aws_region" {
  command = plan
  variables {
    aws_region = "mars-1"
  }
  expect_failures = [var.aws_region]
}

run "environment_is_dev_staging_or_prod" {
  command = plan
  variables {
    environment = "qa"
  }
  expect_failures = [var.environment]
}

run "project_name_is_a_dns_label" {
  command = plan
  variables {
    project = "Collections_Copilot"
  }
  expect_failures = [var.project]
}

run "public_hostname_is_a_hostname" {
  command = plan
  variables {
    public_hostname = "https://collections.example.com/"
  }
  expect_failures = [var.public_hostname]
}

run "smtp_host_is_not_localhost" {
  command = plan
  variables {
    smtp_host = "localhost"
  }
  expect_failures = [var.smtp_host]
}

run "twilio_from_number_is_e164" {
  command = plan
  variables {
    twilio_from_number = "555-0100"
  }
  expect_failures = [var.twilio_from_number]
}

run "twilio_from_number_is_not_the_local_default" {
  command = plan
  variables {
    twilio_from_number = "+15550001000"
  }
  expect_failures = [var.twilio_from_number]
}

run "gateway_image_needs_a_tag_or_digest" {
  command = plan
  variables {
    mcp_gateway_image = "ghcr.io/robertg99/mcp-gateway"
  }
  expect_failures = [var.mcp_gateway_image]
}

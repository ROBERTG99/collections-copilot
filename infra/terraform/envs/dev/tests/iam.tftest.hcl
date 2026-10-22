# Least privilege.
#
# Documented exceptions to "no wildcard actions or resources":
#   1. KMS key policies grant "kms:*" on "*" (this key) to the account root: the standard key
#      policy that keeps the key manageable; IAM then decides who may use it.
#   2. The S3 bucket policy denies "s3:*" to everyone without TLS (a Deny, not a grant).
#   3. AWS-managed policies attached by ARN: AmazonECSTaskExecutionRolePolicy (it includes
#      ecr:GetAuthorizationToken on "*", which ECR requires), ReadOnlyAccess for the plan-only
#      GitHub role (with an explicit deny of data reads), and AmazonRDSEnhancedMonitoringRole.
#   4. Resource suffixes inside one named parent: ":*" on a CloudWatch log group (its streams)
#      and "/*" on an S3 bucket (its objects).
# Every other Allow statement names its actions (no "*" anywhere in an action) and resources.

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

run "no_wildcards_outside_the_exception_list" {
  command = apply

  assert {
    condition = alltrue(flatten([
      for name, doc in merge(module.iam.policy_documents, module.network.policy_documents, module.github_oidc.policy_documents, module.cognito.policy_documents) : [
        for st in jsondecode(doc).Statement : st.Effect != "Allow" || (
          !anytrue([for a in flatten([st.Action]) : strcontains(a, "*")]) &&
          alltrue([for r in flatten([st.Resource]) : !strcontains(r, "*") || (
            startswith(r, "arn:aws:logs:") && endswith(r, ":*") && length(regexall("\\*", r)) == 1) || (
            startswith(r, "arn:aws:s3:::") && endswith(r, "/*") && length(regexall("\\*", r)) == 1)
          ])
        )
      ]
    ]))
    error_message = "An Allow statement uses a wildcard action, or a wildcard resource outside exception 4."
  }
  assert {
    condition     = length(module.iam.policy_documents) >= 7
    error_message = "The IAM module must report its inline policies (five execution roles, api and worker)."
  }
  assert {
    condition = alltrue(flatten([
      for name, doc in module.kms.key_policies : [
        for st in jsondecode(doc).Statement : !contains(flatten([st.Action]), "kms:*") || st.Principal == { AWS = "arn:aws:iam::123456789012:root" }
      ]
    ]))
    error_message = "Only the account root may hold kms:* in a key policy (exception 1)."
  }
  assert {
    condition = alltrue([
      for st in jsondecode(module.s3.policy_documents.uploads_bucket).Statement : st.Effect == "Deny"
    ])
    error_message = "The bucket policy may only deny (exception 2)."
  }
  assert {
    condition = length([
      for st in jsondecode(module.s3.policy_documents.uploads_bucket).Statement : st
      if st.Effect == "Deny" && st.Condition.Bool["aws:SecureTransport"] == "false" &&
      toset(flatten([st.Resource])) == toset([module.s3.bucket_arn, "${module.s3.bucket_arn}/*"])
    ]) == 1
    error_message = "The bucket policy must deny every request without TLS, on the bucket and its objects."
  }
  assert {
    condition = toset(concat(module.iam.managed_policy_arns, module.github_oidc.managed_policy_arns, module.rds.managed_policy_arns)) == toset([
      "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
      "arn:aws:iam::aws:policy/ReadOnlyAccess",
      "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole",
    ])
    error_message = "Only the documented AWS-managed policies may be attached (exception 3)."
  }
}

run "bedrock_and_signing_are_scoped" {
  command = apply

  assert {
    condition = length([
      for st in jsondecode(module.iam.policy_documents["task-worker"]).Statement : st
      if contains(flatten([st.Action]), "bedrock:InvokeModel")
    ]) == 1
    error_message = "The worker needs exactly one statement for Bedrock."
  }
  assert {
    condition = alltrue(flatten([
      for st in jsondecode(module.iam.policy_documents["task-worker"]).Statement : [
        for r in flatten([st.Resource]) : anytrue([for id in var.bedrock_model_ids : endswith(r, "/${id}")])
      ] if contains(flatten([st.Action]), "bedrock:InvokeModel")
    ]))
    error_message = "bedrock:InvokeModel must be limited to the configured model ids."
  }
  assert {
    condition = toset(flatten([
      for name, doc in module.iam.policy_documents : [
        for st in jsondecode(doc).Statement : name if contains(flatten([st.Action]), "kms:Sign")
      ]
    ])) == toset(["task-api", "task-worker"])
    error_message = "kms:Sign is granted to the api (cc-api tokens) and the worker only."
  }
  assert {
    condition = alltrue(flatten([
      for name, doc in module.iam.policy_documents : [
        for st in jsondecode(doc).Statement :
        flatten([st.Resource]) == [module.kms.signing_key_arn] &&
        st.Condition.StringEquals["kms:SigningAlgorithm"] == "RSASSA_PKCS1_V1_5_SHA_256"
        if contains(flatten([st.Action]), "kms:Sign")
      ]
    ]))
    error_message = "kms:Sign must name only the signing key and only RS256's algorithm."
  }
  assert {
    condition     = length([for name, doc in module.iam.policy_documents : name if strcontains(doc, "bedrock:")]) == 1
    error_message = "Only the worker may call Bedrock."
  }
  assert {
    condition     = !contains(keys(module.iam.policy_documents), "task-web") && !contains(keys(module.iam.policy_documents), "task-ledger-mcp")
    error_message = "The web BFF and ledger-mcp call no AWS API and get no task permissions."
  }
}

run "each_execution_role_reads_only_its_own_secrets" {
  command = apply

  assert {
    condition     = length(module.iam.execution_role_arns) == 5
    error_message = "Every service gets its own execution role."
  }
  assert {
    condition     = output.task_secret_names["web"] == tolist(["WEB_SESSION_SECRET"]) && output.task_secret_names["ledger-mcp"] == tolist(["LEDGER_DATABASE_URL"])
    error_message = "The web and ledger-mcp tasks receive only their own secrets."
  }
  assert {
    condition     = !contains(output.task_secret_names["mcp-gateway"], "CC_TWILIO_AUTH_TOKEN") && contains(output.task_secret_names["mcp-gateway"], "MCPGW_DATABASE_URL")
    error_message = "mcp-gateway receives its database URL and webhook secret, never the Twilio token."
  }
  assert {
    condition = alltrue([
      for svc, names in output.task_secret_names : contains(keys(module.iam.policy_documents), "execution-${svc}") == (length(names) > 0)
    ])
    error_message = "A service has a secrets policy exactly when its task definition injects secrets."
  }
}

run "github_plan_role_is_main_only" {
  command = apply

  assert {
    condition     = jsondecode(module.github_oidc.trust_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:ROBERTG99/collections-copilot:ref:refs/heads/main"
    error_message = "Only main of ROBERTG99/collections-copilot may assume the plan role."
  }
  assert {
    condition     = jsondecode(module.github_oidc.trust_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    error_message = "The plan role trusts tokens minted for STS only."
  }
  assert {
    condition = alltrue([
      for a in ["logs:GetLogEvents", "logs:StartQuery", "rds:DownloadDBLogFilePortion", "cognito-idp:ListUsers", "sqs:ReceiveMessage", "ecr:GetDownloadUrlForLayer"] :
      contains(flatten([for st in jsondecode(module.github_oidc.policy_documents.github_plan_deny_data).Statement : st.Action if st.Effect == "Deny"]), a)
    ])
    error_message = "ReadOnlyAccess must come with an explicit deny of data reads (logs, database logs, users, messages, images)."
  }
}

run "role_name_prefixes_fit_every_environment" {
  command = plan
  variables {
    environment = "staging"
  }
  assert {
    condition     = alltrue([for p in module.iam.role_name_prefixes : length(p) <= 38])
    error_message = "An IAM role name_prefix is longer than 38 characters."
  }
}

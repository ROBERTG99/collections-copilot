# envs/dev composes every module: network, keys, data stores, queues, identity, images, the ECS
# cluster with five services (api, worker, web, ledger-mcp, mcp-gateway), the ALB and alarms.
# This configuration is validated and tested offline only (`make tf-check`); it is never applied.

data "aws_caller_identity" "current" {}

locals {
  name     = "${var.project}-${var.environment}"
  services = ["api", "worker", "web", "ledger-mcp", "mcp-gateway"]
  queues = {
    "cc-tasks"   = { visibility_timeout_seconds = 300 }
    "cc-replies" = { visibility_timeout_seconds = 120 }
  }
  # Values are set out of band (docs/runbook.md); Terraform creates empty entries only.
  secret_names = [
    "database-url", "ledger-database-url", "redis-url", "twilio-account-sid", "twilio-auth-token",
    "mcpgw-webhook-secret", "mcpgw-database-url", "web-session-secret",
  ]
  public_url = "https://${var.public_hostname}"
  s          = module.secrets.secret_arns
  app_env = {
    CC_ENV                     = "prod"
    CC_AWS_REGION              = var.aws_region
    CC_SQS_ENDPOINT_URL        = ""
    CC_S3_ENDPOINT_URL         = ""
    CC_S3_BUCKET               = module.s3.bucket_name
    CC_LLM_PROVIDER            = "bedrock"
    CC_BEDROCK_MODEL_FAST      = var.bedrock_model_ids[0]
    CC_BEDROCK_MODEL_REASONING = var.bedrock_model_ids[length(var.bedrock_model_ids) - 1]
    CC_OIDC_ISSUER             = module.cognito.issuer_url
    CC_OIDC_AUDIENCE           = module.cognito.client_id
    CC_PUBLIC_BASE_URL         = local.public_url
    CC_MCPGW_URL               = "http://mcp-gateway:8344/mcp"
    CC_MCPGW_ADMIN_URL         = "http://mcp-gateway-admin:8348"
    CC_TWILIO_BASE_URL         = "https://api.twilio.com"
    CC_TWILIO_FROM             = var.twilio_from_number
    CC_SMTP_HOST               = var.smtp_host
    CC_SMTP_PORT               = tostring(var.smtp_port)
    CC_AGENT_TOKEN_PROVIDER    = "kms"
    CC_AGENT_KMS_KEY_ID        = module.kms.signing_key_arn
  }
  app_secrets = {
    CC_DATABASE_URL         = local.s["database-url"]
    CC_REDIS_URL            = local.s["redis-url"]
    CC_TWILIO_ACCOUNT_SID   = local.s["twilio-account-sid"]
    CC_TWILIO_AUTH_TOKEN    = local.s["twilio-auth-token"]
    CC_MCPGW_WEBHOOK_SECRET = local.s["mcpgw-webhook-secret"]
  }
  http_check_py = "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%d/healthz', timeout=2).status == 200 else 1)"
  service_config = {
    api = {
      image  = "${module.ecr.repository_urls["api"]}:${var.image_tag}"
      port   = 8310
      cpu    = 512
      memory = 1024
      # uvicorn trusts X-Forwarded-For only from the load balancer's subnets (M6 audit M5).
      env       = merge(local.app_env, { FORWARDED_ALLOW_IPS = join(",", module.network.public_subnet_cidrs) })
      secrets   = local.app_secrets
      check     = ["CMD", "python", "-c", format(local.http_check_py, 8310)]
      target    = "api"
      ingress   = [module.alb.security_group_id]
      scaling   = { min = 2, max = 6 }
      extra     = {}
      egress    = true # Twilio, Cognito keys
      ports_out = [5432, 6379, 8344, 8348]
      smtp      = var.smtp_port
    }
    worker = {
      image     = "${module.ecr.repository_urls["worker"]}:${var.image_tag}"
      port      = null
      cpu       = 1024
      memory    = 2048
      env       = local.app_env
      secrets   = local.app_secrets
      check     = ["CMD", "python", "-c", format(local.http_check_py, 8311)]
      target    = null
      ingress   = []
      scaling   = { min = 1, max = var.worker_max_tasks, queue_name = "cc-tasks", backlog_per_task = 20 }
      extra     = {}
      egress    = true # Twilio, Bedrock
      ports_out = [5432, 6379, 8344]
      smtp      = var.smtp_port
    }
    web = {
      image  = "${module.ecr.repository_urls["web"]}:${var.image_tag}"
      port   = 8300
      cpu    = 512
      memory = 1024
      env = {
        NODE_ENV               = "production"
        WEB_PUBLIC_URL         = local.public_url
        WEB_API_BASE_URL       = "http://api:8310"
        WEB_OIDC_ISSUER        = module.cognito.issuer_url
        WEB_OIDC_CLIENT_ID     = module.cognito.client_id
        WEB_OIDC_AUTHORIZE_URL = "${module.cognito.hosted_ui_url}/oauth2/authorize"
        WEB_OIDC_TOKEN_URL     = "${module.cognito.hosted_ui_url}/oauth2/token"
        WEB_OIDC_LOGOUT_URL    = "${module.cognito.hosted_ui_url}/logout"
      }
      secrets   = { WEB_SESSION_SECRET = local.s["web-session-secret"] }
      check     = ["CMD", "node", "-e", "fetch('http://127.0.0.1:8300/healthz').then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"]
      target    = "web"
      ingress   = [module.alb.security_group_id]
      scaling   = { min = 2, max = 4 }
      extra     = {}
      egress    = true # Cognito token endpoint and keys
      ports_out = [8310]
      smtp      = null
    }
    ledger-mcp = {
      image     = "${module.ecr.repository_urls["ledger-mcp"]}:${var.image_tag}"
      port      = 8343
      cpu       = 256
      memory    = 512
      env       = {}
      secrets   = { LEDGER_DATABASE_URL = local.s["ledger-database-url"] }
      check     = ["CMD", "python", "-c", format(local.http_check_py, 8343)]
      target    = null
      ingress   = []
      scaling   = { min = 1, max = 2 }
      extra     = {}
      egress    = false # only the database
      ports_out = [5432]
      smtp      = null
    }
    mcp-gateway = {
      image  = coalesce(var.mcp_gateway_image, "${module.ecr.repository_urls["mcp-gateway"]}:${var.image_tag}")
      port   = 8344
      cpu    = 256
      memory = 512
      # Names as read by mcp-gateway (third_party/mcp-gateway/src/mcpgw_lite/config.py).
      env = {
        MCPGW_UPSTREAMS            = "ledger=http://ledger-mcp:8343/mcp"
        MCPGW_POLICY_FILE          = "/app/deploy/mcpgw/policy/collections.yaml"
        MCPGW_JWKS_URL             = "http://api:8310/.well-known/agent-jwks.json"
        MCPGW_TRUSTED_ISSUERS      = "collections-copilot"
        MCPGW_APPROVAL_WEBHOOK_URL = "http://api:8310/api/v1/webhooks/mcpgw/approvals"
        MCPGW_DEV_ISSUER           = "false"
      }
      secrets = {
        MCPGW_APPROVAL_WEBHOOK_SECRET = local.s["mcpgw-webhook-secret"]
        MCPGW_DATABASE_URL            = local.s["mcpgw-database-url"]
      }
      check     = ["CMD", "python", "-c", format(local.http_check_py, 8344)]
      target    = null
      ingress   = []
      scaling   = { min = 1, max = 2 }
      extra     = { "mcp-gateway-admin" = 8348 } # approvals API, reached by the api only
      egress    = false
      ports_out = [5432, 8343, 8310]
      smtp      = null
    }
  }
}

module "kms" {
  source      = "../../modules/kms"
  project     = var.project
  environment = var.environment
}

module "network" {
  source      = "../../modules/network"
  project     = var.project
  environment = var.environment
  cidr_block  = "10.40.0.0/16"
  azs         = var.availability_zones
  kms_key_arn = module.kms.data_key_arn
}

module "s3" {
  source      = "../../modules/s3"
  bucket_name = "${local.name}-uploads-${data.aws_caller_identity.current.account_id}"
  kms_key_arn = module.kms.data_key_arn
}

module "sqs" {
  source      = "../../modules/sqs"
  queues      = local.queues
  kms_key_arn = module.kms.data_key_arn
}

module "secrets" {
  source       = "../../modules/secrets"
  prefix       = "${var.project}/${var.environment}"
  secret_names = local.secret_names
  kms_key_arn  = module.kms.data_key_arn
}

module "ecr" {
  source       = "../../modules/ecr"
  prefix       = var.project
  repositories = ["api", "worker", "web", "ledger-mcp", "mcp-gateway"]
  kms_key_arn  = module.kms.data_key_arn
}

module "iam" {
  source              = "../../modules/iam"
  environment         = var.environment
  services            = local.services
  service_secret_arns = { for k, v in local.service_config : k => values(v.secrets) }
  data_key_arn        = module.kms.data_key_arn
  signing_key_arn     = module.kms.signing_key_arn
  queue_arns          = module.sqs.queue_arns
  dlq_arns            = module.sqs.dlq_arns
  uploads_bucket_arn  = module.s3.bucket_arn
  bedrock_model_ids   = var.bedrock_model_ids
}

module "cognito" {
  source        = "../../modules/cognito"
  name          = local.name
  domain_prefix = local.name
  callback_urls = ["${local.public_url}/auth/callback"]
  logout_urls   = ["${local.public_url}/"]
  kms_key_arn   = module.kms.data_key_arn
}

module "alb" {
  source            = "../../modules/alb"
  name              = local.name
  vpc_id            = module.network.vpc_id
  vpc_cidr_block    = module.network.vpc_cidr_block
  public_subnet_ids = module.network.public_subnet_ids
  certificate_arn   = var.certificate_arn
  kms_key_arn       = module.kms.data_key_arn
  default_target    = "web"
  targets = {
    web = { port = 8300, health_path = "/healthz" }
    # Twilio's webhooks are the only public route to the API; the gateway's stays in the VPC.
    api = { port = 8310, health_path = "/healthz", path_patterns = ["/api/v1/webhooks/twilio/*"], priority = 10 }
  }
}

module "observability" {
  source             = "../../modules/observability"
  project            = var.project
  environment        = var.environment
  services           = local.services
  kms_key_arn        = module.kms.data_key_arn
  alarm_email        = var.alarm_email
  monthly_budget_usd = var.monthly_budget_usd
  queue_names        = { for k, _ in local.queues : k => k }
  dlq_names          = module.sqs.dlq_names
  alb_arn_suffix     = module.alb.arn_suffix
}

module "github_oidc" {
  source            = "../../modules/github_oidc"
  name              = local.name
  github_repository = "ROBERTG99/collections-copilot"
}

resource "aws_ecs_cluster" "this" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
  depends_on = [aws_cloudwatch_log_group.container_insights]
}

# Created first so Container Insights does not create it without a key or a retention.
resource "aws_cloudwatch_log_group" "container_insights" {
  #checkov:skip=CKV_AWS_338:§16.3 sets 30-day retention for this environment
  name              = "/aws/ecs/containerinsights/${local.name}/performance"
  retention_in_days = 30
  kms_key_id        = module.kms.data_key_arn
}

resource "aws_service_discovery_http_namespace" "this" {
  name        = local.name
  description = "Service Connect namespace for ${local.name}"
}

module "service" {
  source                        = "../../modules/ecs_service"
  for_each                      = local.service_config
  name                          = each.key
  cluster_name                  = aws_ecs_cluster.this.name
  cluster_arn                   = aws_ecs_cluster.this.arn
  image                         = each.value.image
  cpu                           = each.value.cpu
  memory                        = each.value.memory
  container_port                = each.value.port
  environment                   = each.value.env
  secrets                       = each.value.secrets
  health_check_command          = each.value.check
  vpc_id                        = module.network.vpc_id
  vpc_cidr_block                = module.network.vpc_cidr_block
  subnet_ids                    = module.network.private_subnet_ids
  ingress_security_group_ids    = each.value.ingress
  target_group_arn              = each.value.target == null ? null : module.alb.target_group_arns[each.value.target]
  execution_role_arn            = module.iam.execution_role_arns[each.key]
  task_role_arn                 = module.iam.task_role_arns[each.key]
  log_group_name                = module.observability.log_group_names[each.key]
  service_connect_namespace_arn = aws_service_discovery_http_namespace.this.arn
  autoscaling                   = each.value.scaling
  extra_ports                   = each.value.extra
  internet_egress               = each.value.egress
  vpc_egress_ports              = each.value.ports_out
  smtp_egress_port              = each.value.smtp
  s3_prefix_list_id             = module.network.s3_prefix_list_id
}

# Service-to-service paths (Service Connect): web → api, api and worker → mcp-gateway, api →
# the gateway's approvals API, mcp-gateway → ledger-mcp, and mcp-gateway → api (its approval
# webhook and the agent JWKS).
locals {
  internal_paths = {
    "web-to-api"               = { from = "web", to = "api", port = 8310 }
    "api-to-mcp-gateway"       = { from = "api", to = "mcp-gateway", port = 8344 }
    "api-to-mcp-gateway-admin" = { from = "api", to = "mcp-gateway", port = 8348 }
    "worker-to-mcp-gateway"    = { from = "worker", to = "mcp-gateway", port = 8344 }
    "gateway-to-ledger-mcp"    = { from = "mcp-gateway", to = "ledger-mcp", port = 8343 }
    "gateway-to-api"           = { from = "mcp-gateway", to = "api", port = 8310 }
  }
}

resource "aws_vpc_security_group_ingress_rule" "internal" {
  for_each                     = local.internal_paths
  security_group_id            = module.service[each.value.to].security_group_id
  referenced_security_group_id = module.service[each.value.from].security_group_id
  description                  = "${each.value.from} to ${each.value.to}"
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
}

module "rds" {
  source                     = "../../modules/rds"
  name                       = local.name
  vpc_id                     = module.network.vpc_id
  subnet_ids                 = module.network.private_subnet_ids
  allowed_security_group_ids = { for s in ["api", "worker", "ledger-mcp", "mcp-gateway"] : s => module.service[s].security_group_id }
  kms_key_arn                = module.kms.data_key_arn
}

# One execution role (reads only that service's secrets) and one task role per service. Every
# statement names its resources; the only wildcard is inside AWS's managed execution-role policy
# (envs/dev/tests/iam.tftest.hcl).

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account = data.aws_caller_identity.current.account_id
  region  = data.aws_region.current.region

  ecs_trust = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = local.account } }
    }]
  })

  queue_arns = values(var.queue_arns)
  dlq_arns   = values(var.dlq_arns)
  model_arns = flatten([
    for id in var.bedrock_model_ids : [
      "arn:aws:bedrock:${local.region}::foundation-model/${id}",
      "arn:aws:bedrock:${local.region}:${local.account}:inference-profile/${id}",
    ]
  ])
  data_key_use = {
    Sid      = "UseDataKey"
    Effect   = "Allow"
    Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
    Resource = [var.data_key_arn]
  }

  # Services whose task definition injects no secret get the managed policy only.
  execution_policies = {
    for svc, arns in var.service_secret_arns : svc => jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Sid      = "ReadTaskSecrets"
          Effect   = "Allow"
          Action   = ["secretsmanager:GetSecretValue"]
          Resource = sort(distinct(arns))
        },
        {
          Sid      = "DecryptTaskSecrets"
          Effect   = "Allow"
          Action   = ["kms:Decrypt"]
          Resource = [var.data_key_arn]
        },
      ]
    }) if length(arns) > 0
  }

  # Agent JWTs are RS256 (KMS RSASSA_PKCS1_V1_5_SHA_256); no other algorithm may be used.
  sign_agent_tokens = {
    Sid       = "SignAgentTokens"
    Effect    = "Allow"
    Action    = ["kms:Sign"]
    Resource  = [var.signing_key_arn]
    Condition = { StringEquals = { "kms:SigningAlgorithm" = "RSASSA_PKCS1_V1_5_SHA_256" } }
  }

  task_policies = {
    api = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Sid      = "EnqueueTasks"
          Effect   = "Allow"
          Action   = ["sqs:SendMessage", "sqs:GetQueueUrl", "sqs:GetQueueAttributes"]
          Resource = local.queue_arns
        },
        {
          Sid      = "ReadDlqDepth"
          Effect   = "Allow"
          Action   = ["sqs:GetQueueUrl", "sqs:GetQueueAttributes"]
          Resource = local.dlq_arns
        },
        {
          Sid      = "Uploads"
          Effect   = "Allow"
          Action   = ["s3:GetObject", "s3:PutObject"]
          Resource = ["${var.uploads_bucket_arn}/*"]
        },
        {
          Sid      = "ListUploads"
          Effect   = "Allow"
          Action   = ["s3:ListBucket"]
          Resource = [var.uploads_bucket_arn]
        },
        {
          Sid      = "PublishAgentJwks"
          Effect   = "Allow"
          Action   = ["kms:GetPublicKey"]
          Resource = [var.signing_key_arn]
        },
        # cc-api tokens with scope mcpgw:approve for a supervisor's promise approval.
        local.sign_agent_tokens,
        local.data_key_use,
      ]
    })
    worker = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Sid    = "ConsumeQueues"
          Effect = "Allow"
          Action = [
            "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility",
            "sqs:SendMessage", "sqs:GetQueueUrl", "sqs:GetQueueAttributes",
          ]
          Resource = local.queue_arns
        },
        {
          Sid      = "WatchDlqs"
          Effect   = "Allow"
          Action   = ["sqs:GetQueueUrl", "sqs:GetQueueAttributes"]
          Resource = local.dlq_arns
        },
        {
          Sid      = "InvokeConfiguredModels"
          Effect   = "Allow"
          Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
          Resource = local.model_arns
        },
        local.sign_agent_tokens,
        local.data_key_use,
      ]
    })
  }
  # Services that call no AWS API get a role with no permissions.
  roles = toset(var.services)
}

resource "aws_iam_role" "execution" {
  for_each           = local.roles
  name_prefix        = "cc-${var.environment}-${each.key}-exec-" # 38 characters at most
  assume_role_policy = local.ecs_trust
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  for_each   = local.roles
  role       = aws_iam_role.execution[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution" {
  for_each = local.execution_policies
  role     = aws_iam_role.execution[each.key].id
  policy   = each.value
}

resource "aws_iam_role" "task" {
  for_each           = local.roles
  name_prefix        = "cc-${var.environment}-${each.key}-task-" # 38 characters at most
  assume_role_policy = local.ecs_trust
}

resource "aws_iam_role_policy" "task" {
  for_each = { for k, v in local.task_policies : k => v if contains(var.services, k) }
  role     = aws_iam_role.task[each.key].id
  policy   = each.value
}

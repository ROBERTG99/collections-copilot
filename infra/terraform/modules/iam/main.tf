# ECS execution role and one task role per service. Every statement names its resources; the
# only wildcard is inside AWS's managed execution-role policy (envs/dev/tests/iam.tftest.hcl).

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account = data.aws_caller_identity.current.account_id
  region  = data.aws_region.current.region
  name    = "${var.project}-${var.environment}"

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

  execution_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadTaskSecrets"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = values(var.secret_arns)
      },
      {
        Sid      = "DecryptTaskSecrets"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = [var.data_key_arn]
      },
    ]
  })

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
        {
          Sid      = "SignAgentTokens"
          Effect   = "Allow"
          Action   = ["kms:Sign"]
          Resource = [var.signing_key_arn]
        },
        local.data_key_use,
      ]
    })
  }
  # Services that call no AWS API get a role with no permissions.
  roles = toset(var.services)
}

resource "aws_iam_role" "execution" {
  name_prefix        = "${local.name}-exec-"
  assume_role_policy = local.ecs_trust
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution" {
  role   = aws_iam_role.execution.id
  policy = local.execution_policy
}

resource "aws_iam_role" "task" {
  for_each           = local.roles
  name_prefix        = "${local.name}-${each.key}-"
  assume_role_policy = local.ecs_trust
}

resource "aws_iam_role_policy" "task" {
  for_each = { for k, v in local.task_policies : k => v if contains(var.services, k) }
  role     = aws_iam_role.task[each.key].id
  policy   = each.value
}

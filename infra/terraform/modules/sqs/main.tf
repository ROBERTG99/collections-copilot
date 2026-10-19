# Work queues with dead-letter queues: redrive after `max_receive_count` receives, KMS on both.

resource "aws_sqs_queue" "dlq" {
  for_each                          = var.queues
  name                              = "${each.key}-dlq"
  kms_master_key_id                 = var.kms_key_arn
  kms_data_key_reuse_period_seconds = 300
  message_retention_seconds         = 1209600 # 14 days, time to replay (docs/runbook.md)
}

resource "aws_sqs_queue" "this" {
  for_each                          = var.queues
  name                              = each.key
  kms_master_key_id                 = var.kms_key_arn
  kms_data_key_reuse_period_seconds = 300
  visibility_timeout_seconds        = each.value.visibility_timeout_seconds
  message_retention_seconds         = each.value.message_retention_seconds
  receive_wait_time_seconds         = 20
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq[each.key].arn
    maxReceiveCount     = var.max_receive_count
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  for_each  = var.queues
  queue_url = aws_sqs_queue.dlq[each.key].id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.this[each.key].arn]
  })
}

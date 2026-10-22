# Log groups (30 days, KMS), an SNS topic for alarms, the SLO alarms and a monthly budget.

resource "aws_cloudwatch_log_group" "service" {
  #checkov:skip=CKV_AWS_338:30-day retention is the documented policy for this environment (log_retention_days)
  for_each          = toset(var.services)
  name              = "/${var.project}/${var.environment}/${each.key}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

resource "aws_sns_topic" "alarms" {
  name              = "${var.project}-${var.environment}-alarms"
  kms_master_key_id = var.kms_key_arn
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# CCDLQNotEmpty: any message in a dead-letter queue for 5 minutes.
resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  for_each            = var.dlq_names
  alarm_name          = "${var.project}-${var.environment}-dlq-not-empty-${each.key}"
  alarm_description   = "Dead-letter queue ${each.value} is not empty (docs/runbook.md, DLQ replay)."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = each.value }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 5
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "oldest_message" {
  for_each            = var.queue_names
  alarm_name          = "${var.project}-${var.environment}-oldest-message-${each.key}"
  alarm_description   = "Oldest message in ${each.value} waited more than 300 s: the worker is not keeping up."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  dimensions          = { QueueName = each.value }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 5
  threshold           = 300
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

# 5xx above 1% of requests (target and load balancer errors).
resource "aws_cloudwatch_metric_alarm" "http_5xx" {
  alarm_name          = "${var.project}-${var.environment}-5xx-rate"
  alarm_description   = "More than 1% of requests failed with 5xx for 10 minutes."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 10
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  metric_query {
    id          = "rate"
    expression  = "100 * (FILL(target5xx, 0) + FILL(lb5xx, 0)) / IF(requests > 0, requests, 1)"
    label       = "5xx percent"
    return_data = true
  }
  metric_query {
    id = "target5xx"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_Target_5XX_Count"
      dimensions  = { LoadBalancer = var.alb_arn_suffix }
      period      = 60
      stat        = "Sum"
    }
  }
  metric_query {
    id = "lb5xx"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_ELB_5XX_Count"
      dimensions  = { LoadBalancer = var.alb_arn_suffix }
      period      = 60
      stat        = "Sum"
    }
  }
  metric_query {
    id = "requests"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "RequestCount"
      dimensions  = { LoadBalancer = var.alb_arn_suffix }
      period      = 60
      stat        = "Sum"
    }
  }
}

resource "aws_budgets_budget" "monthly" {
  name         = "${var.project}-${var.environment}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alarm_email]
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alarm_email]
  }
}

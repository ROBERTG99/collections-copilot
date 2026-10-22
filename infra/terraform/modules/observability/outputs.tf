output "alarm_topic_arn" {
  description = "SNS topic for alarms."
  value       = aws_sns_topic.alarms.arn
}

output "log_group_names" {
  description = "Log group name per service."
  value       = { for k, g in aws_cloudwatch_log_group.service : k => g.name }
}

output "log_groups" {
  description = "Retention and KMS key per log group (for the tests)."
  value       = { for k, g in aws_cloudwatch_log_group.service : k => { retention = g.retention_in_days, kms = g.kms_key_id } }
}

output "alarm_names" {
  description = "Every alarm this module creates."
  value = concat(
    [for a in aws_cloudwatch_metric_alarm.dlq_not_empty : a.alarm_name],
    [for a in aws_cloudwatch_metric_alarm.oldest_message : a.alarm_name],
    [aws_cloudwatch_metric_alarm.http_5xx.alarm_name],
  )
}

output "alarm_thresholds" {
  description = "Thresholds of the SLO alarms (for the tests)."
  value = {
    dlq            = [for a in aws_cloudwatch_metric_alarm.dlq_not_empty : a.threshold]
    oldest_message = [for a in aws_cloudwatch_metric_alarm.oldest_message : a.threshold]
    http_5xx_pct   = aws_cloudwatch_metric_alarm.http_5xx.threshold
  }
}

output "topic_kms" {
  description = "KMS key of the alarm topic."
  value       = aws_sns_topic.alarms.kms_master_key_id
}

output "budget_usd" {
  description = "Monthly budget limit."
  value       = aws_budgets_budget.monthly.limit_amount
}

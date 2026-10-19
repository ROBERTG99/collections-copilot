output "queue_urls" {
  description = "Queue URLs by name."
  value       = { for k, q in aws_sqs_queue.this : k => q.url }
}

output "queue_arns" {
  description = "Queue ARNs by name."
  value       = { for k, q in aws_sqs_queue.this : k => q.arn }
}

output "dlq_names" {
  description = "Dead-letter queue names by source queue."
  value       = { for k, q in aws_sqs_queue.dlq : k => q.name }
}

output "dlq_arns" {
  description = "Dead-letter queue ARNs by source queue."
  value       = { for k, q in aws_sqs_queue.dlq : k => q.arn }
}

output "encryption" {
  description = "KMS key of every queue and DLQ (for the encryption tests)."
  value = merge(
    { for k, q in aws_sqs_queue.this : k => q.kms_master_key_id },
    { for k, q in aws_sqs_queue.dlq : "${k}-dlq" => q.kms_master_key_id },
  )
}

output "redrive" {
  description = "maxReceiveCount per queue."
  value       = { for k, q in aws_sqs_queue.this : k => jsondecode(q.redrive_policy).maxReceiveCount }
}

output "execution_role_arn" {
  description = "ECS task execution role."
  value       = aws_iam_role.execution.arn
}

output "task_role_arns" {
  description = "Task role ARN per service."
  value       = { for k, r in aws_iam_role.task : k => r.arn }
}

output "policy_documents" {
  description = "Every inline identity policy created here, by name (for the IAM tests)."
  value = merge(
    { execution = local.execution_policy },
    { for k, v in local.task_policies : "task-${k}" => v if contains(var.services, k) },
  )
}

output "trust_policy" {
  description = "Trust policy shared by the execution and task roles."
  value       = local.ecs_trust
}

output "managed_policy_arns" {
  description = "AWS-managed policies attached (documented exceptions)."
  value       = [aws_iam_role_policy_attachment.execution_managed.policy_arn]
}

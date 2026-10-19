output "execution_role_arns" {
  description = "ECS task execution role per service."
  value       = { for k, r in aws_iam_role.execution : k => r.arn }
}

output "task_role_arns" {
  description = "Task role ARN per service."
  value       = { for k, r in aws_iam_role.task : k => r.arn }
}

output "policy_documents" {
  description = "Every inline identity policy created here, by name (for the IAM tests)."
  value = merge(
    { for k, p in aws_iam_role_policy.execution : "execution-${k}" => p.policy },
    { for k, p in aws_iam_role_policy.task : "task-${k}" => p.policy },
  )
}

output "trust_policy" {
  description = "Trust policy shared by the execution and task roles."
  value       = local.ecs_trust
}

output "managed_policy_arns" {
  description = "AWS-managed policies attached (documented exceptions)."
  value       = distinct([for a in aws_iam_role_policy_attachment.execution_managed : a.policy_arn])
}

output "role_name_prefixes" {
  description = "Name prefixes of every execution and task role (IAM allows 38 characters)."
  value       = concat([for r in aws_iam_role.execution : r.name_prefix], [for r in aws_iam_role.task : r.name_prefix])
}

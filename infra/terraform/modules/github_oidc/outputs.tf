output "role_arn" {
  description = "Plan-only role for GitHub Actions (vars.AWS_PLAN_ROLE_ARN)."
  value       = aws_iam_role.plan.arn
}

output "trust_policy" {
  description = "Trust policy (for the IAM tests)."
  value       = local.trust
}

output "policy_documents" {
  description = "Inline policies (for the IAM tests)."
  value       = local.state_policy == null ? {} : { github_plan_state = local.state_policy }
}

output "managed_policy_arns" {
  description = "AWS-managed policies attached (documented exceptions)."
  value       = [aws_iam_role_policy_attachment.read_only.policy_arn]
}

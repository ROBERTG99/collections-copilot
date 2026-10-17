# Secret entries only: Terraform never holds a value. Operators set each value after the first
# plan is applied (docs/runbook.md, "Secret rotation"), and ECS injects them into tasks.

resource "aws_secretsmanager_secret" "this" {
  #checkov:skip=CKV2_AWS_57:third-party credentials (Twilio, gateway HMAC, session key) are rotated by the runbook procedure, not a Lambda
  for_each                = toset(var.secret_names)
  name                    = "${var.prefix}/${each.key}"
  description             = "collections-copilot ${each.key} (value set out of band)"
  kms_key_id              = var.kms_key_arn
  recovery_window_in_days = var.recovery_window_days
}

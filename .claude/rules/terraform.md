---
paths:
  - "infra/**"
---
# Terraform rules

- Never run `terraform apply` or `terraform destroy` (the hook blocks them). `command = apply` appears only inside `terraform test` with `mock_provider`.
- No wildcard IAM actions outside the documented exception list in `envs/dev/tests/iam.tftest.hcl`.
- KMS encryption everywhere: RDS, S3, SQS, CloudWatch logs, Secrets Manager.
- Every variable has a `description`, a `type` and, where meaningful, a `validation` block.
- IAM policies use `jsonencode` so tests can read them. Run `make tf-check` before committing.

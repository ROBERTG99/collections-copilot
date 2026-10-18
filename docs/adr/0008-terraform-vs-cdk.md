# 0008. Terraform versus CDK

- Status: accepted
- Date: 2026-10-17

## Context

FR-15 asks for complete AWS infrastructure that is never applied: no `terraform apply` or `destroy`, no deployment to a cloud account and no real credentials (spec §0, rule 8; `CLAUDE.md`, Never). The configuration must still be checked, so its tests have to run offline and without an AWS account (spec Appendix A). Reviewers need to read the security-relevant parts, IAM policies and encryption, directly in the source. In the build container registry.terraform.io is blocked and releases.hashicorp.com is reachable (spec Appendix A).

## Decision

- The infrastructure is Terraform in `infra/terraform/`: thirteen modules under `infra/terraform/modules/` composed by `infra/terraform/envs/dev/main.tf`, with Terraform `>= 1.9`, the AWS provider `~> 6.68` and the archive provider for the Cognito trigger (`infra/terraform/envs/dev/versions.tf:1-12`). The S3 backend is written out but commented (`infra/terraform/envs/dev/versions.tf:14-21`).
- Tests are `terraform test` files in `infra/terraform/envs/dev/tests/` that use `mock_provider "aws"` (for example `infra/terraform/envs/dev/tests/iam.tftest.hcl:14`). `command = apply` appears only inside those tests, against the mock provider, so nothing is created.
- `make tf-check` runs `fmt`, `init -backend=false`, `validate`, `test`, tflint and checkov offline (`scripts/tf.sh:14-26`). The provider comes from Terraform's implicit local mirror, filled from releases.hashicorp.com by `scripts/install-tools.sh` (docs/DECISIONS.md, 2026-10-17).
- IAM policies are written with `jsonencode` so the tests can decode and assert on them (`.claude/rules/terraform.md`). `tests/harness/test_terraform_wiring.py` checks every `CC_*`, `MCPGW_*` and `WEB_*` name and the webhook and JWKS paths in `infra/terraform/envs/dev/main.tf` against the code (docs/DECISIONS.md, 2026-10-18).
- A guard hook blocks `terraform apply` and `destroy` in agent sessions (`.claude/hooks/pre_tool_guard.py:19-20`), and the only cloud job is a read-only plan on `main` through GitHub OIDC (docs/DECISIONS.md, 2026-10-17).

## Alternatives considered

- **AWS CDK.** Rejected per §5 (stack table: "`mock_provider` tests"). CDK synthesises CloudFormation, and its assertions check the synthesised template, in which values AWS computes at deploy time are unresolved tokens. Deploying a CDK app normally also needs a bootstrap stack in the target account. The policies and conditions a reviewer must check would be built by program code in a general-purpose language, and the existing tflint, checkov and Dependabot (`.github/dependabot.yml`, ecosystem `terraform`) setup would have to be replaced.

## Consequences

- The whole configuration is validated and tested without credentials, in CI and in the cloud container.
- `mock_provider` returns made-up values, so tests prove the configuration's logic, not AWS behaviour. Two wiring mistakes passed every Terraform test until the harness test compared the configuration with the code (`tests/harness/test_terraform_wiring.py:1-2`; PROGRESS.md).
- The configuration has never been planned against an account (docs/KNOWN_LIMITATIONS.md, Terraform scope). The first plan with the GitHub plan role is the check that what passes offline is accepted by AWS.
- Provider upgrades need the local mirror updated as well as the lock file, which holds hashes for linux and darwin on amd64 and arm64 (docs/DECISIONS.md, 2026-10-18).

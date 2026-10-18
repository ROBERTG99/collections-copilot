# 0001. Amazon Bedrock as the primary provider versus the direct Anthropic API

- Status: accepted
- Date: 2026-10-16

## Context

Every model call goes through one gateway, `src/collections_copilot/llm/gateway.py` (Appendix B). `CC_LLM_PROVIDER` selects `fake` (the default, offline), `anthropic` (the Claude API) or `bedrock` (`src/collections_copilot/config.py:52`, `src/collections_copilot/llm/providers/__init__.py:39-48`). The same models exist on both hosts under different IDs: `claude-opus-5-5` and `claude-haiku-5-5` on the Claude API, `anthropic.claude-opus-5-5` and `anthropic.claude-haiku-5-5` on Bedrock (`src/collections_copilot/config.py:53-56`).

The production target is AWS (`infra/terraform/envs/dev`). There, every other dependency is reached with IAM task roles, KMS keys and VPC endpoints, and the only secrets are the ones listed in `infra/terraform/envs/dev/main.tf:15-18`. The worker sends redacted notes and replies to the provider on every call (`src/collections_copilot/llm/redaction.py`; docs/security.md, residual risks). The Claude API authenticates with a long-lived `ANTHROPIC_API_KEY`.

## Decision

- In AWS the worker uses Bedrock. The task environment sets `CC_LLM_PROVIDER = "bedrock"` and takes `CC_BEDROCK_MODEL_FAST` and `CC_BEDROCK_MODEL_REASONING` from `bedrock_model_ids` (`infra/terraform/envs/dev/main.tf:27-29`). That variable must be non-empty and every ID must start with `anthropic.` (`infra/terraform/envs/dev/variables.tf:88-94`).
- Access is granted by IAM, not by a key. The worker task role may call `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` only on the configured model IDs (`infra/terraform/modules/iam/main.tf:26-28`, `infra/terraform/modules/iam/main.tf:128`), which `infra/terraform/envs/dev/tests/iam.tftest.hcl` checks. The network module creates a `bedrock-runtime` interface endpoint (`infra/terraform/modules/network/main.tf:9`). No provider key is among the Secrets Manager entries.
- The Claude API stays a supported provider behind the same adapter and request shape (`src/collections_copilot/llm/providers/anthropic_sdk.py:28`, `:94`, `:101`; ADR 0002). It is the host for `make eval-live` and `make record-fixtures` (`src/collections_copilot/evals/live.py:358`), because a developer machine can hold an API key without an AWS account.
- The gateway picks the model IDs for the selected provider in one place (`src/collections_copilot/llm/gateway.py:68-72`). There is no failover between providers at runtime: when calls fail, the circuit breaker opens and callers use the deterministic fallbacks of §4.6.

## Alternatives considered

- **The direct Anthropic API as the production provider.** Rejected. It adds a long-lived API key to Secrets Manager, with its own rotation, and a second outbound path for debtor text next to the AWS account's controls. IAM could not limit which task may call which model, so the assertion on model access in `infra/terraform/envs/dev/tests/iam.tftest.hcl` would have no equivalent. Its advantage, server-side refusal fallbacks, is kept where it is used (see Consequences).
- **Bedrock first with automatic failover to the Claude API.** Rejected. It needs both credentials and both egress paths in production, and it would send debtor text to a second processor exactly when the first one is failing. The deterministic fallbacks already keep a case moving during an outage, labelled as fallbacks, and they always reach a human (docs/DECISIONS.md, 2026-10-16, auto-approval only for `source == "llm"`).

## Consequences

- Revoking or narrowing model access in AWS is an IAM change, and no provider key exists to leak.
- Server-side refusal fallbacks (`fallbacks: "default"`) are sent only to the Claude API for `claude-opus-5-5` (`src/collections_copilot/llm/providers/anthropic_sdk.py:25`, `:54`; docs/DECISIONS.md, 2026-10-16). On Bedrock a refusal falls back to the deterministic rules or templates.
- Live eval results come from the Claude API host. They measure the same models, but they are not a measurement of Bedrock.
- Cost accounting uses the Claude API list prices as an estimate for the `anthropic.` IDs, because Bedrock is partner-priced (`src/collections_copilot/llm/pricing.yaml:2`).
- The `anthropic.` prefix rule excludes cross-region inference profiles such as `us.anthropic.…` (docs/KNOWN_LIMITATIONS.md, Identity (AWS)).
- The configuration has never been planned or applied against an account (docs/KNOWN_LIMITATIONS.md, Terraform scope), so the first real Bedrock call will be the first check that the granted actions and the endpoint cover the Mantle client's requests. If they do not, or if Bedrock stops offering a model or a request feature the routes need, this decision should be revisited.

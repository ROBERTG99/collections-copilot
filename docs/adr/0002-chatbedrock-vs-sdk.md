# 0002. ChatBedrock versus the Anthropic SDK in graph nodes

- Status: accepted
- Date: 2026-10-16

## Context

Graph nodes reach Claude only through `collections_copilot.llm.gateway`. On Amazon Bedrock there are two candidate clients: langchain-aws `ChatBedrockConverse`, which fits the LangGraph ecosystem, and the official SDK's Bedrock Mantle client (`AsyncAnthropicBedrockMantle`), which serves the same Messages API as the Claude API. The spec allows ChatBedrock only if `tests/unit/llm/test_chatbedrock_conformance.py` proves Appendix B compliance.

## Decision

Use the SDK Mantle client for Bedrock (`llm/providers/anthropic_sdk.py::bedrock_mantle`) and the SDK's `AsyncAnthropic` for the Claude API. Both share one adapter and one request shape: a cached system prompt, a single user turn, `thinking: {type: "adaptive"}`, an explicit `output_config.effort` per route, and `output_config.format` JSON schema. ChatBedrock stays implemented (`llm/providers/chatbedrock.py`) behind the conformance probe.

The offline probe against the installed langchain-aws (1.9.0) records nine checks (`uv run pytest tests/unit/llm/test_chatbedrock_conformance.py`):

- Seven pass. `with_structured_output(method="json_schema")` sends Bedrock's native `outputConfig.textFormat` with no forced `toolChoice`; adaptive thinking and effort pass through `additionalModelRequestFields`; the refusal `stopReason` is visible.
- `function_calling_fails_loudly_without_tool_call` fails. `method="function_calling"` sends `toolChoice=auto` (forced choice is rejected on `claude-opus-5-5`) and silently returns `None` when the model answers in text.
- `refusal_category_available` fails. Converse does not return `stop_details.category`, which the gateway logs for refusals.

## Alternatives considered

- **ChatBedrockConverse with `method="json_schema"` everywhere.** Rejected. It would satisfy most checks, but it loses the refusal category and adds a second request shape and a second set of error types to the gateway. It also leaves the `function_calling` trap one argument away.
- **Raw `bedrock-runtime` `InvokeModel`.** Rejected: no typed errors, and every request shape would be hand-written.

## Consequences

- One adapter covers both the Claude API and Bedrock, and it is tested with recorded responses for both hosts (`tests/unit/llm/test_providers_recorded.py`).
- Server-side refusal fallbacks (`fallbacks: "default"`) are used on the Claude API for `claude-opus-5-5`. On Bedrock a refusal falls back to the deterministic rules or templates.
- If langchain-aws starts returning the refusal category and fails loudly on missing tool calls, the conformance test fails, and this ADR should be revisited.

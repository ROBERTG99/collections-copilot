---
paths:
  - "src/collections_copilot/llm/**"
  - "src/collections_copilot/agent/**"
  - "prompts/**"
---
# LLM code rules

- Load the `claude-api` skill before writing code that calls Claude.
- Only `collections_copilot.llm` imports provider SDKs (`anthropic`, `langchain_aws`); import-linter enforces it.
- Model IDs come from settings (`CC_MODEL_FAST`, `CC_MODEL_REASONING`); defaults `claude-haiku-5-5` and `claude-opus-5-5`, Bedrock `anthropic.`-prefixed IDs. Never append date suffixes.
- Structured output via `output_config.format` JSON schema; no forced `tool_choice`, no assistant prefill.
- Adaptive thinking with an explicit `output_config.effort` per route; never send `budget_tokens`.
- Check `stop_reason` (`refusal`, `max_tokens`) before reading content; retry only 408/409/429/5xx/connection errors.
- Prompts live in `prompts/<name>.v<N>.md`; untrusted notes and replies only inside `<untrusted_note>` blocks after the instructions.
- Every call records prompt version and hash, model, tokens, cost, latency and outcome as a step record.
- Validate every output against its Pydantic schema; one retry with the validation error, then the deterministic fallback.
- PII is redacted before any provider call; tests use `FakeLLM` and recorded cassettes, never live calls.

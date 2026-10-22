# LLM fixtures

`messages_*.json` are Messages API response bodies used by `tests/unit/llm/test_providers_recorded.py`.
They were written by hand from the documented response shape (no provider key was used to
record them); `make record-fixtures` replaces them with live recordings when `ANTHROPIC_API_KEY`
is set. `cassettes/<route>/<sha256>.json` are request-keyed recordings that `FakeLLM` replays.

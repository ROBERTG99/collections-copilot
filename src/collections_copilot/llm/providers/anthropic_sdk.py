"""Claude via the official `anthropic` SDK: the Claude API (`AsyncAnthropic`) or Amazon Bedrock
through the Mantle client (`AsyncAnthropicBedrockMantle`, ADR 0002). Same Messages API shape.

Request shape (Appendix B): stable system prompt marked for prompt caching, one user turn (no
assistant prefill), adaptive thinking with an explicit per-route effort, structured output via
`output_config.format` (no forced tool_choice). SDK retries are disabled; the gateway retries.
"""

from __future__ import annotations

from typing import Any

import anthropic

from collections_copilot.llm.providers.base import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    Usage,
    is_retryable_status,
)

SERVER_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Server-side refusal fallbacks are available on the Claude API for these models only.
FALLBACK_MODELS = frozenset({"claude-opus-5-5"})


def build_params(request: ProviderRequest) -> dict[str, Any]:
    return {
        "model": request.model,
        "max_tokens": request.max_tokens,
        "system": [
            {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
        ],
        "messages": [{"role": "user", "content": request.user}],
        "thinking": {"type": "adaptive"},
        "output_config": {
            "effort": request.effort,
            "format": {"type": "json_schema", "schema": request.json_schema},
        },
    }


class AnthropicSdkProvider:
    def __init__(
        self,
        client: anthropic.AsyncAnthropic | anthropic.AsyncAnthropicBedrockMantle,
        *,
        name: str = "anthropic",
        server_fallbacks: bool = True,
    ) -> None:
        self.client = client
        self.name = name
        self.server_fallbacks = server_fallbacks and name == "anthropic"

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        params = build_params(request)
        try:
            if self.server_fallbacks and request.model in FALLBACK_MODELS:
                message: Any = await self.client.beta.messages.create(
                    **params, betas=[SERVER_FALLBACK_BETA], fallbacks="default"
                )
            else:
                message = await self.client.messages.create(**params)
        except anthropic.APIConnectionError as exc:  # includes APITimeoutError
            raise ProviderError(f"connection error: {type(exc).__name__}", retryable=True) from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(
                f"{exc.status_code} {type(exc).__name__}",
                status=exc.status_code,
                retryable=is_retryable_status(exc.status_code),
            ) from exc
        text = "".join(getattr(b, "text", "") for b in message.content if b.type == "text")
        usage = message.usage
        details = getattr(message, "stop_details", None)
        return ProviderResponse(
            text=text,
            stop_reason=str(message.stop_reason),
            model=str(message.model),
            usage=Usage(
                input_tokens=int(usage.input_tokens or 0),
                output_tokens=int(usage.output_tokens or 0),
                cache_read_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
                cache_creation_input_tokens=int(
                    getattr(usage, "cache_creation_input_tokens", 0) or 0
                ),
            ),
            refusal_category=getattr(details, "category", None) if details else None,
        )


def anthropic_api(timeout_s: float, http_client: Any | None = None) -> AnthropicSdkProvider:
    kwargs: dict[str, Any] = {"max_retries": 0, "timeout": timeout_s}
    if http_client is not None:
        kwargs["http_client"] = http_client
    return AnthropicSdkProvider(anthropic.AsyncAnthropic(**kwargs), name="anthropic")


def bedrock_mantle(
    region: str, timeout_s: float, http_client: Any | None = None
) -> AnthropicSdkProvider:
    kwargs: dict[str, Any] = {"aws_region": region, "max_retries": 0, "timeout": timeout_s}
    if http_client is not None:
        kwargs["http_client"] = http_client
    return AnthropicSdkProvider(anthropic.AsyncAnthropicBedrockMantle(**kwargs), name="bedrock")

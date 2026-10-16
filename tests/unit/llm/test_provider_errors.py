"""M2 review: provider errors map to retryable only for transport failures and 408/409/429/5xx."""

from __future__ import annotations

from typing import Any

import pytest

from collections_copilot.llm.providers.base import ProviderError, ProviderRequest
from collections_copilot.llm.providers.chatbedrock import ChatBedrockProvider


class ClientError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"status {status}")
        self.response = {"ResponseMetadata": {"HTTPStatusCode": status}}


class EndpointConnectionError(Exception):
    pass


class Boom:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def bind(self, **_: Any) -> Boom:
        return self

    async def ainvoke(self, _: Any) -> Any:
        raise self.exc


def request() -> ProviderRequest:
    return ProviderRequest(
        route="classify_reply",
        model="anthropic.claude-haiku-5-5",
        system="s",
        user="u",
        json_schema={"type": "object"},
        effort="low",
        max_tokens=100,
        timeout_s=5.0,
    )


@pytest.mark.parametrize(
    ("exc", "retryable"),
    [
        (ClientError(429), True),
        (ClientError(503), True),
        (ClientError(400), False),
        (ClientError(403), False),
        (EndpointConnectionError("refused"), True),
        (ValueError("bad output"), False),
    ],
)
async def test_bedrock_error_mapping(exc: Exception, retryable: bool) -> None:
    provider = ChatBedrockProvider(lambda _: Boom(exc))  # type: ignore[arg-type,return-value]
    with pytest.raises(ProviderError) as info:
        await provider.complete(request())
    assert info.value.retryable is retryable

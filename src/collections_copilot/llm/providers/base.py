"""Provider interface: one request in, one response out. Only `collections_copilot.llm` imports SDKs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Effort = Literal["low", "medium", "high", "xhigh", "max"]


@dataclass(frozen=True)
class ProviderRequest:
    route: str
    model: str
    system: str
    user: str
    json_schema: dict[str, Any]
    effort: Effort
    max_tokens: int
    timeout_s: float = 20.0


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    stop_reason: str
    model: str
    usage: Usage = field(default_factory=Usage)
    refusal_category: str | None = None


class ProviderError(Exception):
    """Provider failure; `retryable` is True only for 408, 409, 429, 5xx and connection errors."""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


RETRYABLE_STATUS = frozenset({408, 409, 429})


def is_retryable_status(status: int | None) -> bool:
    return status is None or status in RETRYABLE_STATUS or status >= 500


class Provider(Protocol):
    name: str

    async def complete(self, request: ProviderRequest) -> ProviderResponse: ...

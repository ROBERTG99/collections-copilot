"""SmsSender interface (Twilio adapter in twilio.py; in-memory double for tests and evals)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class SmsResult:
    provider_sid: str
    status: str


class SmsError(Exception):
    def __init__(self, message: str, *, retryable: bool, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class SmsSender(Protocol):
    async def send(self, to_e164: str, body: str, *, idempotency_key: str) -> SmsResult: ...


@dataclass
class InMemorySmsSender:
    """Records sends; never contacts anyone."""

    sent: list[dict[str, str]] = field(default_factory=list)
    fail_with: SmsError | None = None

    async def send(self, to_e164: str, body: str, *, idempotency_key: str) -> SmsResult:
        if self.fail_with is not None:
            raise self.fail_with
        sid = "SM" + uuid.uuid4().hex
        self.sent.append({"to": to_e164, "body": body, "key": idempotency_key, "sid": sid})
        return SmsResult(provider_sid=sid, status="queued")

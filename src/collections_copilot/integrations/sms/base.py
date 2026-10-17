"""SmsSender interface (Twilio adapter in twilio.py; in-memory double for tests and evals)."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Protocol

# The synthetic ranges this project may text (CLAUDE.md "Never"): +1 555… and +34 555….
SYNTHETIC = re.compile(r"^\+(?:1555|34555)\d{5,10}$")


def is_synthetic(e164: str) -> bool:
    return bool(SYNTHETIC.match(e164))


@dataclass(frozen=True)
class SmsResult:
    provider_sid: str
    status: str


class SmsError(Exception):
    """`retryable`: the provider certainly did not take the message, so a resend is safe.
    `maybe_sent`: the provider may have taken it (timeout, 5xx, unreadable 2xx); the send counts
    toward the 7-day contact cap even though it is marked failed."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        status: int | None = None,
        maybe_sent: bool = False,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.maybe_sent = maybe_sent


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

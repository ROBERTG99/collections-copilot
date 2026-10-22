"""A send is retried only when Twilio certainly did not receive it (at-most-once)."""

from __future__ import annotations

import httpx
import pytest

from collections_copilot.integrations.sms.base import SmsError
from collections_copilot.integrations.sms.twilio import TwilioSmsSender


def sender(handler: object) -> TwilioSmsSender:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return TwilioSmsSender("http://twilio.test", "AC1", "token", "+15550000000", client=client)


def raising(exc: Exception) -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


@pytest.mark.parametrize(
    ("handler", "retryable"),
    [
        (raising(httpx.ConnectError("refused")), True),
        (raising(httpx.ConnectTimeout("connect timeout")), True),
        (lambda r: httpx.Response(429), True),
        (lambda r: httpx.Response(503), True),
        (lambda r: httpx.Response(500), False),
        (lambda r: httpx.Response(502), False),
        (lambda r: httpx.Response(201, text="<html>"), False),
        (lambda r: httpx.Response(400, json={"code": 21211}), False),
        # The request may have reached Twilio: never resend blindly.
        (raising(httpx.ReadTimeout("read timeout")), False),
        (raising(httpx.RemoteProtocolError("disconnected")), False),
        (raising(httpx.WriteTimeout("write timeout")), False),
    ],
)
async def test_retry_classification(handler: object, retryable: bool) -> None:
    with pytest.raises(SmsError) as info:
        await sender(handler).send("+15550000001", "hi", idempotency_key="sms:x:1")
    assert info.value.retryable is retryable


async def test_accepted_message_returns_sid() -> None:
    ok = sender(lambda r: httpx.Response(201, json={"sid": "SM123", "status": "queued"}))
    result = await ok.send("+15550000001", "hi", idempotency_key="sms:x:1")
    assert result.provider_sid == "SM123"


@pytest.mark.parametrize(
    ("handler", "maybe_sent"),
    [
        (raising(httpx.ConnectError("refused")), False),
        (lambda r: httpx.Response(429), False),
        (lambda r: httpx.Response(400), False),
        (raising(httpx.ReadTimeout("read timeout")), True),
        (lambda r: httpx.Response(502), True),
        (lambda r: httpx.Response(201, text="<html>"), True),
    ],
)
async def test_maybe_sent_counts_toward_the_cap(handler: object, maybe_sent: bool) -> None:
    with pytest.raises(SmsError) as info:
        await sender(handler).send("+15550000001", "hi", idempotency_key="sms:x:1")
    assert info.value.maybe_sent is maybe_sent


async def test_real_numbers_are_refused_before_any_request() -> None:
    """Synthetic ranges only, checked at the last step before Twilio."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(201, json={"sid": "SM1", "status": "queued"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    guarded = TwilioSmsSender(
        "http://twilio.test", "AC1", "token", "+15550000000", client=client, synthetic_only=True
    )
    with pytest.raises(SmsError) as err:
        await guarded.send("+447700900123", "hi", idempotency_key="k")
    assert not err.value.retryable
    assert not calls
    await guarded.send("+34555000111", "hi", idempotency_key="k2")
    assert len(calls) == 1


async def test_error_bodies_never_carry_the_recipient() -> None:
    """Twilio's 4xx body names the To number; it is masked before the
    error text is stored on the message or a follow-up."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"code": 21211, "message": "The 'To' number +15550001234 is not valid."}
        )

    with pytest.raises(SmsError) as err:
        await sender(handler).send("+15550001234", "hi", idempotency_key="k")
    assert "+15550001234" not in str(err.value)

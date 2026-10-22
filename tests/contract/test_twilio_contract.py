"""FR-7: the Twilio adapter against the WireMock double, and webhook signatures against
the official `twilio` library (the debtor simulator signs with it)."""

from __future__ import annotations

import os
from urllib.parse import parse_qsl

import httpx
import pytest
from twilio.request_validator import RequestValidator

from collections_copilot.integrations.sms.base import SmsError
from collections_copilot.integrations.sms.signature import compute_signature, is_valid
from collections_copilot.integrations.sms.twilio import TwilioSmsSender

pytestmark = [pytest.mark.contract, pytest.mark.integration]
WIREMOCK = os.environ.get("CC_TWILIO_BASE_URL", "http://127.0.0.1:8342")
SID = "AC00000000000000000000000000000000"
TOKEN = "dev-twilio-auth-token"


def sender(token: str = TOKEN) -> TwilioSmsSender:
    return TwilioSmsSender(
        WIREMOCK, SID, token, "+15550001000", status_callback="http://api.test/status"
    )


async def journal_body(sid: str) -> dict[str, str]:
    async with httpx.AsyncClient() as client:
        entries = (await client.get(f"{WIREMOCK}/__admin/requests")).json()["requests"]
    for entry in entries:
        if sid in entry.get("response", {}).get("body", ""):
            return dict(parse_qsl(entry["request"]["body"]))
    raise AssertionError("request not in the WireMock journal")


async def test_create_message_returns_sid_and_sends_form() -> None:
    result = await sender().send("+15550002002", "Hi. Reply STOP to opt out.", idempotency_key="k1")
    assert result.provider_sid.startswith("SM")
    assert result.status == "queued"
    form = await journal_body(result.provider_sid)
    assert form == {
        "To": "+15550002002",
        "From": "+15550001000",
        "Body": "Hi. Reply STOP to opt out.",
        "StatusCallback": "http://api.test/status",
    }


async def test_unavailable_is_retryable_and_bad_auth_is_not() -> None:
    with pytest.raises(SmsError) as unavailable:
        await sender().send("+15559990500", "probe", idempotency_key="k2")
    assert unavailable.value.status == 503
    assert unavailable.value.retryable
    assert not unavailable.value.maybe_sent
    with pytest.raises(SmsError) as unauthorized:
        await sender("wrong-token").send("+15550002002", "x", idempotency_key="k3")
    assert unauthorized.value.status == 401
    assert not unauthorized.value.retryable


@pytest.mark.parametrize(
    "params",
    [
        {"MessageSid": "SM1", "MessageStatus": "delivered", "To": "+15550002002"},
        {"Body": "Puedo pagar 1.234,56 el viernes 🙂", "From": "+34555123456", "MessageSid": "SM2"},
        {},
    ],
)
def test_signature_matches_the_twilio_library(params: dict[str, str]) -> None:
    url = "http://api:8310/api/v1/webhooks/twilio/inbound"
    expected = RequestValidator(TOKEN).compute_signature(url, params)
    assert compute_signature(url, params.items(), TOKEN) == expected
    assert is_valid(url, params.items(), expected, TOKEN)
    assert not is_valid(url, {**params, "Body": "STOP"}.items(), expected, TOKEN)
    assert not is_valid(url + "?x=1", params.items(), expected, TOKEN)
    assert not is_valid(url, params.items(), None, TOKEN)

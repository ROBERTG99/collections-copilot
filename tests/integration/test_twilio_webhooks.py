"""FR-7, FR-8: Twilio status and inbound webhooks."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from collections_copilot.db.models import AuditEvent, Message, MessageStatusEvent
from collections_copilot.db.repositories import Repos
from collections_copilot.integrations.sms.base import InMemorySmsSender
from collections_copilot.integrations.sms.signature import compute_signature
from tests.factories import make_account
from tests.integration.test_approval_flow import RecordingQueue

pytestmark = pytest.mark.integration
STATUS = "/api/v1/webhooks/twilio/status"
INBOUND = "/api/v1/webhooks/twilio/inbound"


@pytest.fixture
async def doubles(api_app: Any) -> AsyncIterator[tuple[RecordingQueue, InMemorySmsSender]]:
    real = api_app.state.queues, api_app.state.sms
    api_app.state.queues, api_app.state.sms = RecordingQueue(), InMemorySmsSender()
    try:
        yield api_app.state.queues, api_app.state.sms
    finally:
        api_app.state.queues, api_app.state.sms = real


async def post_signed(
    client: httpx.AsyncClient, api_app: Any, path: str, params: dict[str, str], *, bad: bool = False
) -> httpx.Response:
    settings = api_app.state.settings
    url = f"{settings.public_base_url.rstrip('/')}{path}"
    signature = compute_signature(url, params.items(), settings.twilio_auth_token)
    if bad:
        signature = signature[:-4] + "AAAA"
    return await client.post(path, data=params, headers={"X-Twilio-Signature": signature})


async def outbound(sm: Any, tenant: uuid.UUID, **account: Any) -> tuple[uuid.UUID, Message]:
    account_id = await make_account(sm, tenant, **account)
    async with sm() as s, s.begin():
        repos = Repos(s)
        acc = await repos.accounts.get(tenant, account_id)
        assert acc is not None
        case = await repos.cases.create(tenant, account_id, status="awaiting_reply")
        msg, _ = await repos.messages.claim(
            tenant,
            f"sms:{case.id}:1",
            account_id=account_id,
            case_id=case.id,
            direction="outbound",
            to_e164=acc.phone_e164,
            body="Reminder. Reply STOP to opt out.",
            status="queued",
            provider_sid="SM" + uuid.uuid4().hex,
        )
        return account_id, msg


async def _count(sm: Any, *where: Any, model: Any = Message) -> int:
    async with sm() as s:
        stmt = select(func.count()).select_from(model).where(*where)
        return int((await s.execute(stmt)).scalar_one())


async def test_duplicate_status_single_row(client, api_app, sessionmaker, tenant, doubles) -> None:  # type: ignore[no-untyped-def]
    _, msg = await outbound(sessionmaker, tenant)
    params = {"MessageSid": msg.provider_sid, "MessageStatus": "delivered", "To": msg.to_e164}
    for _ in range(3):
        resp = await post_signed(client, api_app, STATUS, params)
        assert resp.status_code == 204, resp.text
    late = {**params, "MessageStatus": "sent"}  # out of order: never moves a message back
    assert (await post_signed(client, api_app, STATUS, late)).status_code == 204
    assert (
        await _count(
            sessionmaker,
            MessageStatusEvent.provider_sid == msg.provider_sid,
            model=MessageStatusEvent,
        )
        == 2
    )
    async with sessionmaker() as s:
        row = await Repos(s).messages.get(tenant, msg.id)
    assert row is not None
    assert row.status == "delivered"


async def test_bad_signature_403_and_audited(
    client, api_app, sessionmaker, tenant, doubles
) -> None:  # type: ignore[no-untyped-def]
    _, msg = await outbound(sessionmaker, tenant)
    params = {"MessageSid": msg.provider_sid, "MessageStatus": "failed"}
    resp = await post_signed(client, api_app, STATUS, params, bad=True)
    assert resp.status_code == 403
    assert resp.json()["correlation_id"]
    unsigned = await client.post(STATUS, data=params)
    assert unsigned.status_code == 403
    assert (
        await _count(
            sessionmaker,
            AuditEvent.tenant_id == tenant,
            AuditEvent.action == "webhook.bad_signature",
            model=AuditEvent,
        )
        == 1  # both are 403; the audit is throttled to one event per tenant and minute
    )
    weird = await client.post(
        STATUS, data=params, headers={"X-Twilio-Signature": "é".encode("latin-1")}
    )
    assert weird.status_code == 403  # non-ASCII signature: 403, never a 500
    async with sessionmaker() as s:
        row = await Repos(s).messages.get(tenant, msg.id)
    assert row is not None
    assert row.status == "queued"  # nothing changed
    forged_reply = {"MessageSid": "SM" + uuid.uuid4().hex, "From": msg.to_e164, "Body": "STOP"}
    resp = await post_signed(client, api_app, INBOUND, forged_reply, bad=True)
    assert resp.status_code == 403
    async with sessionmaker() as s:
        assert not await Repos(s).opt_outs.is_opted_out(tenant, msg.to_e164 or "")


async def test_inbound_stop_is_handled_by_rule(
    client, api_app, sessionmaker, tenant, doubles
) -> None:  # type: ignore[no-untyped-def]
    queue, sms = doubles
    account_id, msg = await outbound(sessionmaker, tenant)
    params = {
        "MessageSid": "SM" + uuid.uuid4().hex,
        "From": msg.to_e164,
        "To": "+15550001000",
        "Body": "Please stop texting me",
    }
    for _ in range(2):  # Twilio retries the webhook
        assert (await post_signed(client, api_app, INBOUND, params)).status_code == 204
    assert queue.sent == []  # never reaches the reply graph or a model
    assert len(sms.sent) == 1  # one confirmation
    async with sessionmaker() as s:
        repos = Repos(s)
        assert await repos.opt_outs.is_opted_out(tenant, msg.to_e164 or "")
        case = await repos.cases.get(tenant, msg.case_id)
        assert case is not None
        assert case.status == "closed"
        inbound = [
            m
            for m in await repos.messages.for_account(tenant, account_id)
            if m.direction == "inbound"
        ]
    assert [(m.classification, m.status) for m in inbound] == [("stop", "handled")]
    # The console labels the opt-out as made by the rule, not "source not recorded".
    assert inbound[0].extracted == {"promise": None, "source": "rule"}


async def test_inbound_reply_is_queued_once_stored(
    client, api_app, sessionmaker, tenant, doubles
) -> None:  # type: ignore[no-untyped-def]
    queue, sms = doubles
    _, msg = await outbound(sessionmaker, tenant)
    sid = "SM" + uuid.uuid4().hex
    params = {
        "MessageSid": sid,
        "From": msg.to_e164,
        "To": "+15550001000",
        "Body": "I can pay 1,200 on Friday",
    }
    for _ in range(2):
        assert (await post_signed(client, api_app, INBOUND, params)).status_code == 204
    assert await _count(sessionmaker, Message.provider_sid == sid) == 1
    assert [q["provider_sid"] for q in queue.sent] == [sid, sid]  # the reply graph dedupes
    assert queue.sent[0]["tenant_id"] == str(tenant)
    assert sms.sent == []
    unknown = {**params, "MessageSid": "SM" + uuid.uuid4().hex, "From": "+15559998888"}
    assert (await post_signed(client, api_app, INBOUND, unknown)).status_code == 204
    assert len(queue.sent) == 2


async def test_same_number_in_two_tenants(client, api_app, sessionmaker, tenant, doubles) -> None:  # type: ignore[no-untyped-def]
    """A shared number must never route one tenant's debtor reply to another."""
    from tests.factories import make_tenant

    queue, sms = doubles
    phone = f"+1555{uuid.uuid4().int % 10_000_000:07d}"
    other = await make_tenant(sessionmaker)
    await outbound(sessionmaker, tenant, phone_e164=phone)
    await outbound(sessionmaker, other, phone_e164=phone)
    reply = {
        "MessageSid": "SM" + uuid.uuid4().hex,
        "From": phone,
        "To": "+15550001000",
        "Body": "I can pay 1,200 on Friday",
    }
    assert (await post_signed(client, api_app, INBOUND, reply)).status_code == 204
    assert queue.sent == []  # ambiguous: not attributed to either tenant
    assert await _count(sessionmaker, Message.provider_sid == reply["MessageSid"]) == 0
    for t in (tenant, other):
        assert (
            await _count(
                sessionmaker,
                AuditEvent.tenant_id == t,
                AuditEvent.action == "reply.unattributed",
                model=AuditEvent,
            )
            == 1
        )
    stop = {**reply, "MessageSid": "SM" + uuid.uuid4().hex, "Body": "STOP"}
    assert (await post_signed(client, api_app, INBOUND, stop)).status_code == 204
    async with sessionmaker() as s:
        repos = Repos(s)
        assert await repos.opt_outs.is_opted_out(tenant, phone)
        assert await repos.opt_outs.is_opted_out(other, phone)  # STOP binds every tenant
    assert len(sms.sent) == 1  # one confirmation, not one per tenant
    assert queue.sent == []


async def test_stop_from_holder_without_outbound(
    client, api_app, sessionmaker, tenant, doubles
) -> None:  # type: ignore[no-untyped-def]
    _, sms = doubles
    account = await make_account(sessionmaker, tenant)
    async with sessionmaker() as s:
        acc = await Repos(s).accounts.get(tenant, account)
    assert acc is not None
    stop = {"MessageSid": "SM" + uuid.uuid4().hex, "From": acc.phone_e164, "Body": "STOP"}
    assert (await post_signed(client, api_app, INBOUND, stop)).status_code == 204
    async with sessionmaker() as s:
        assert await Repos(s).opt_outs.is_opted_out(tenant, acc.phone_e164)
    assert len(sms.sent) == 1

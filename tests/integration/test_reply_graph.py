"""T-23: reply graph (FR-8). STOP is handled by rule before any model call."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collections_copilot.agent.reply_graph import build_reply_graph
from collections_copilot.db.repositories import Repos
from collections_copilot.integrations.queue.messages import ReplyMessage
from collections_copilot.integrations.sms.base import InMemorySmsSender
from collections_copilot.llm.providers.fake import FakeLLM
from tests.factories import make_account
from tests.integration.graph_support import FIXED_NOW, make_services

pytestmark = pytest.mark.integration


async def inbound(
    sm: async_sessionmaker[AsyncSession],
    tenant: uuid.UUID,
    account: uuid.UUID,
    body: str,
    case_id: uuid.UUID | None = None,
) -> ReplyMessage:
    sid = "SM" + uuid.uuid4().hex
    async with sm() as s, s.begin():
        repos = Repos(s)
        acc = await repos.accounts.get(tenant, account)
        assert acc is not None
        msg, _ = await repos.messages.add_inbound(
            tenant,
            provider_sid=sid,
            account_id=account,
            case_id=case_id,
            from_e164=acc.phone_e164,
            to_e164="+15550001000",
            body=body,
            status="received",
        )
        return ReplyMessage(
            tenant_id=str(tenant),
            message_id=str(msg.id),
            provider_sid=sid,
            from_e164=acc.phone_e164,
            body=body,
            received_at=FIXED_NOW,
        )


async def open_case_with_draft(
    sm: async_sessionmaker[AsyncSession], tenant: uuid.UUID, account: uuid.UUID
) -> uuid.UUID:
    async with sm() as s, s.begin():
        repos = Repos(s)
        case = await repos.cases.create(tenant, account, status="awaiting_approval")
        await repos.drafts.add(tenant, case.id, version=1, body="Hi", language="en", author="ai")
        return case.id


@pytest.mark.parametrize("body", ["STOP", " stop ", "Baja", "UNSUBSCRIBE."])
async def test_stop_without_llm_call(sessionmaker, database_url, tenant, body: str) -> None:  # type: ignore[no-untyped-def]
    account = await make_account(sessionmaker, tenant)
    case_id = await open_case_with_draft(sessionmaker, tenant, account)
    llm = FakeLLM()
    sms = InMemorySmsSender()
    services = make_services(sessionmaker, database_url, llm, sms)
    graph = build_reply_graph(services, sessionmaker)
    reply = await inbound(sessionmaker, tenant, account, body)
    result: dict[str, Any] = await graph.ainvoke(
        {"reply": reply.model_dump(mode="json", by_alias=True)}
    )
    assert result["label"] == "stop"
    assert llm.requests == []  # no model call, no LLM step record
    assert len(sms.sent) == 1  # one confirmation
    again = await graph.ainvoke(
        {
            "reply": (await inbound(sessionmaker, tenant, account, "STOP")).model_dump(
                mode="json", by_alias=True
            )
        }
    )
    assert again["label"] == "stop"
    assert len(sms.sent) == 1  # still one confirmation
    async with sessionmaker() as s:
        repos = Repos(s)
        acc = await repos.accounts.get(tenant, account)
        assert acc is not None
        assert acc.opted_out_at is not None
        assert await repos.opt_outs.is_opted_out(tenant, acc.phone_e164)
        drafts = await repos.drafts.for_case(tenant, case_id)
        assert [d.status for d in drafts] == ["cancelled"]
        steps = await repos.steps.for_case(tenant, case_id)
        assert not [st for st in steps if st.kind == "llm"]


async def test_dispute_cancels_open_drafts(sessionmaker, database_url, tenant) -> None:  # type: ignore[no-untyped-def]
    account = await make_account(
        sessionmaker,
        tenant,
        invoices=[
            {"amount_cents": 50_000, "days_past_due": 30, "number": f"INV-{uuid.uuid4().hex[:6]}"}
        ],
    )
    case_id = await open_case_with_draft(sessionmaker, tenant, account)
    llm = FakeLLM()
    graph = build_reply_graph(make_services(sessionmaker, database_url, llm), sessionmaker)
    reply = await inbound(
        sessionmaker,
        tenant,
        account,
        "We never received these goods, the invoice is incorrect.",
        case_id,
    )
    result = await graph.ainvoke({"reply": reply.model_dump(mode="json", by_alias=True)})
    assert result["label"] == "dispute"
    assert [r.route for r in llm.requests] == ["classify_reply"]
    async with sessionmaker() as s:
        repos = Repos(s)
        assert [d.status for d in await repos.drafts.for_case(tenant, case_id)] == ["cancelled"]
        assert {i.status for i in await repos.invoices.for_account(tenant, account)} == {"disputed"}
        msg = await repos.messages.get(tenant, reply.message_id)
        assert msg is not None
        assert msg.classification == "dispute"


async def test_paid_and_wrong_number_and_other(sessionmaker, database_url, tenant) -> None:  # type: ignore[no-untyped-def]
    account = await make_account(sessionmaker, tenant)
    graph = build_reply_graph(make_services(sessionmaker, database_url), sessionmaker)
    paid = await graph.ainvoke(
        {
            "reply": (
                await inbound(sessionmaker, tenant, account, "We already paid last week")
            ).model_dump(mode="json", by_alias=True)
        }
    )
    assert paid["label"] == "paid"
    wrong = await graph.ainvoke(
        {
            "reply": (
                await inbound(
                    sessionmaker, tenant, account, "Wrong number, I don't know this company"
                )
            ).model_dump(mode="json", by_alias=True)
        }
    )
    assert wrong["label"] == "wrong_number"
    other = await graph.ainvoke(
        {
            "reply": (await inbound(sessionmaker, tenant, account, "Who is this?")).model_dump(
                mode="json", by_alias=True
            )
        }
    )
    assert other["label"] == "other"
    assert other["outcome"] == "analyst"
    async with sessionmaker() as s:
        repos = Repos(s)
        acc = await repos.accounts.get(tenant, account)
        assert acc is not None
        assert acc.contact_flag == "wrong_number"
        kinds = {f.kind for f in await repos.follow_ups.list(tenant)}
        assert "payment_verification" in kinds


async def test_reclassify_records_correction_only(client, sessionmaker, tenant, auth) -> None:  # type: ignore[no-untyped-def]
    account = await make_account(sessionmaker, tenant)
    reply = await inbound(sessionmaker, tenant, account, "Who is this?")
    url = f"/api/v1/replies/{reply.message_id}:reclassify"
    resp = await client.post(
        url, json={"label": "dispute", "note": "mentions damaged goods"}, headers=auth("analyst")
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["classification"] == "dispute"
    stop = await client.post(url, json={"label": "stop"}, headers=auth("analyst"))
    assert stop.status_code == 409
    async with sessionmaker() as s:
        repos = Repos(s)
        assert {i.status for i in await repos.invoices.for_account(tenant, account)} == {"open"}
    other = await client.post(
        url, json={"label": "other"}, headers=auth("analyst", tenant_id=uuid.uuid4())
    )
    assert other.status_code == 404

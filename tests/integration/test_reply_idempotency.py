"""Regressions: replies, STOP confirmations, promise write-backs and case runs are
idempotent under at-least-once delivery and concurrent workers."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from collections_copilot.agent.nodes.record_promise import retry_write_back
from collections_copilot.agent.reply_actions import apply_stop, stop_confirmation_key
from collections_copilot.agent.reply_graph import build_reply_graph
from collections_copilot.db.models import AuditEvent, FollowUp, Note
from collections_copilot.db.repositories import Repos
from collections_copilot.db.session import advisory_lock
from collections_copilot.integrations.mcp.ledger import LedgerResult
from collections_copilot.integrations.queue.messages import TaskMessage
from collections_copilot.integrations.sms.base import InMemorySmsSender
from collections_copilot.llm.providers.base import ProviderRequest, ProviderResponse
from collections_copilot.llm.providers.fake import FakeLLM
from collections_copilot.worker.runner import CaseRunner
from tests.factories import make_account
from tests.integration.graph_support import FIXED_NOW, case_graph, make_services, new_case
from tests.integration.test_reply_graph import inbound

pytestmark = pytest.mark.integration


async def _count(sm: Any, model: Any, *where: Any) -> int:
    async with sm() as s:
        return int(
            (await s.execute(select(func.count()).select_from(model).where(*where))).scalar_one()
        )


async def test_paid_reply_redelivered_applies_once(sessionmaker, database_url, tenant) -> None:  # type: ignore[no-untyped-def]
    account = await make_account(sessionmaker, tenant)
    llm = FakeLLM()
    graph = build_reply_graph(make_services(sessionmaker, database_url, llm), sessionmaker)
    reply = (await inbound(sessionmaker, tenant, account, "We already paid last week")).model_dump(
        mode="json", by_alias=True
    )
    first = await graph.ainvoke({"reply": reply})
    second = await graph.ainvoke({"reply": reply})
    assert first["outcome"] == "verify_payment"
    assert second["outcome"] == "duplicate"
    assert [r.route for r in llm.requests] == ["classify_reply"]
    assert await _count(sessionmaker, FollowUp, FollowUp.account_id == account) == 1
    assert await _count(sessionmaker, Note, Note.account_id == account) == 1


class CrashOnNoteAdd(Exception):
    pass


async def test_crash_after_classification_reuses_label(
    sessionmaker, database_url, tenant, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    account = await make_account(sessionmaker, tenant)
    llm = FakeLLM()
    graph = build_reply_graph(make_services(sessionmaker, database_url, llm), sessionmaker)
    reply = (
        await inbound(sessionmaker, tenant, account, "We never received these goods")
    ).model_dump(mode="json", by_alias=True)
    from collections_copilot.db.repositories.accounts import NotesRepo

    original = NotesRepo.add

    async def crash(*args: Any, **kwargs: Any) -> Any:
        raise CrashOnNoteAdd

    monkeypatch.setattr(NotesRepo, "add", crash)
    with pytest.raises(CrashOnNoteAdd):
        await graph.ainvoke({"reply": reply})
    monkeypatch.setattr(NotesRepo, "add", original)
    result = await graph.ainvoke({"reply": reply})
    assert result["outcome"] == "disputed"
    assert [r.route for r in llm.requests] == ["classify_reply"]  # not classified twice
    assert await _count(sessionmaker, Note, Note.account_id == account) == 1


async def test_promise_without_amount_goes_to_analyst(sessionmaker, database_url, tenant) -> None:  # type: ignore[no-untyped-def]
    account = await make_account(sessionmaker, tenant)

    class NoAmount(FakeLLM):
        async def complete(self, request: ProviderRequest) -> ProviderResponse:
            date = (FIXED_NOW.date() + timedelta(days=7)).isoformat()
            body = (
                '{"label": "promise_to_pay", "confidence": 0.9, "promise": '
                f'{{"amount_cents": null, "promised_date": "{date}"}}}}'
            )
            return ProviderResponse(body, "end_turn", "fake")

    services = make_services(sessionmaker, database_url, NoAmount())
    graph = build_reply_graph(services, sessionmaker)
    reply = await inbound(sessionmaker, tenant, account, "I will pay next week")
    result = await graph.ainvoke({"reply": reply.model_dump(mode="json", by_alias=True)})
    assert result["outcome"] == "analyst"
    assert "no amount" in result["promise"]["problems"]
    async with sessionmaker() as s:
        assert await Repos(s).promises.for_account(tenant, account) == []


async def test_stop_confirmation_is_stored_and_sent_once(sessionmaker, tenant) -> None:  # type: ignore[no-untyped-def]
    account = await make_account(sessionmaker, tenant)
    async with sessionmaker() as s:
        acc = await Repos(s).accounts.get(tenant, account)
    assert acc is not None
    sms = InMemorySmsSender()
    assert await apply_stop(
        sessionmaker, sms, tenant_id=tenant, phone_e164=acc.phone_e164, now=FIXED_NOW
    )
    assert not await apply_stop(
        sessionmaker, sms, tenant_id=tenant, phone_e164=acc.phone_e164, now=FIXED_NOW
    )
    assert len(sms.sent) == 1
    async with sessionmaker() as s:
        row = await Repos(s).messages.by_key(tenant, stop_confirmation_key(tenant, acc.phone_e164))
    assert row is not None
    assert row.direction == "outbound"
    assert row.provider_sid == sms.sent[0]["sid"]
    assert row.body == sms.sent[0]["body"]


async def test_stop_confirmation_recovered_after_crash_before_send(sessionmaker, tenant) -> None:  # type: ignore[no-untyped-def]
    """Crash after the opt-out committed but before the send was attempted: a redelivered STOP
    sends the stored confirmation; an attempted one is never resent."""
    account = await make_account(sessionmaker, tenant)
    async with sessionmaker() as s:
        acc = await Repos(s).accounts.get(tenant, account)
    assert acc is not None
    key = stop_confirmation_key(tenant, acc.phone_e164)
    async with sessionmaker() as s, s.begin():
        repos = Repos(s)
        await repos.opt_outs.add(tenant, acc.phone_e164, source="sms_stop")
        await repos.messages.claim(
            tenant,
            key,
            account_id=account,
            direction="outbound",
            to_e164=acc.phone_e164,
            body="You are unsubscribed. Reply STOP to opt out.",
            status="pending",
        )
    sms = InMemorySmsSender()
    await apply_stop(sessionmaker, sms, tenant_id=tenant, phone_e164=acc.phone_e164, now=FIXED_NOW)
    await apply_stop(sessionmaker, sms, tenant_id=tenant, phone_e164=acc.phone_e164, now=FIXED_NOW)
    assert len(sms.sent) == 1


class FakeLedger:
    def __init__(self, result: LedgerResult) -> None:
        self.result = result
        self.calls = 0

    async def record_promise_to_pay(
        self, tenant: str, args: dict[str, Any], **_: Any
    ) -> LedgerResult:
        self.calls += 1
        await asyncio.sleep(0.05)
        return self.result

    async def get_balance(self, tenant: str, account_ref: str) -> dict[str, Any]:
        return {}


async def _held_promise(sm: Any, tenant: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    account = await make_account(sm, tenant)
    async with sm() as s, s.begin():
        promise = await Repos(s).promises.create_if_absent(
            tenant,
            account_id=account,
            amount_cents=10_000,
            currency="USD",
            promised_date=FIXED_NOW.date() + timedelta(days=5),
            invoice_numbers=["INV-1"],
            status="approval_required",
            mcp_approval_id="apr_1",
            idempotency_key=f"ptp:{tenant}:SM{uuid.uuid4().hex}",
        )
        return account, promise.id


async def test_concurrent_write_back_records_once(sessionmaker, database_url, tenant) -> None:  # type: ignore[no-untyped-def]
    _, promise_id = await _held_promise(sessionmaker, tenant)
    services = make_services(sessionmaker, database_url)
    services.ledger = FakeLedger(LedgerResult("recorded", ledger_ref="L-1"))
    outcomes = await asyncio.gather(
        retry_write_back(services, sessionmaker, str(tenant), str(promise_id)),
        retry_write_back(services, sessionmaker, str(tenant), str(promise_id)),
    )
    assert sorted(outcomes) == ["promise_recorded", "promise_recorded"]
    assert await _count(sessionmaker, FollowUp, FollowUp.promise_id == promise_id) == 1
    assert (
        await _count(
            sessionmaker,
            AuditEvent,
            AuditEvent.entity_id == str(promise_id),
            AuditEvent.action == "promise.recorded",
        )
        == 1
    )


async def test_late_failure_never_overwrites_recorded(sessionmaker, database_url, tenant) -> None:  # type: ignore[no-untyped-def]
    _, promise_id = await _held_promise(sessionmaker, tenant)
    services = make_services(sessionmaker, database_url)
    services.ledger = FakeLedger(LedgerResult("recorded", ledger_ref="L-1"))
    await retry_write_back(services, sessionmaker, str(tenant), str(promise_id))
    from collections_copilot.agent.nodes import record_promise as rp

    outcome = await rp._apply(
        services,
        sessionmaker,
        tenant,
        promise_id,
        uuid.uuid4(),
        None,
        LedgerResult("unavailable", detail="timeout"),
        "test",
    )
    assert outcome == "promise_recorded"
    async with sessionmaker() as s:
        promise = await Repos(s).promises.get(tenant, promise_id)
    assert promise is not None
    assert promise.status == "recorded"


PLAN_NOTE = "Owner asked to split the balance into installments; cash flow is tight this quarter."


async def test_duplicate_concurrent_deliveries_run_once(sessionmaker, database_url, tenant) -> None:  # type: ignore[no-untyped-def]
    account = await make_account(sessionmaker, tenant, notes=[PLAN_NOTE])
    case_id = await new_case(sessionmaker, tenant, account)
    llm = FakeLLM()
    async with case_graph(make_services(sessionmaker, database_url, llm), database_url) as graph:
        runner = CaseRunner(graph, lock=lambda key: advisory_lock(sessionmaker, key))
        task = TaskMessage(tenant_id=str(tenant), kind="process_case", case_id=case_id)
        outcomes = await asyncio.gather(runner.handle(task), runner.handle(task))
    assert sorted(outcomes) == ["duplicate", "started"]
    assert sum(1 for r in llm.requests if r.route == "next_best_action") == 1


async def test_resume_without_kind_is_rejected(sessionmaker, database_url, tenant) -> None:  # type: ignore[no-untyped-def]
    account = await make_account(sessionmaker, tenant, notes=[PLAN_NOTE])
    case_id = await new_case(sessionmaker, tenant, account)
    async with case_graph(make_services(sessionmaker, database_url), database_url) as graph:
        runner = CaseRunner(graph)
        await runner.handle(
            TaskMessage(tenant_id=str(tenant), kind="process_case", case_id=case_id)
        )
        with pytest.raises(ValueError, match="kind"):
            await runner.handle(
                TaskMessage(
                    tenant_id=str(tenant),
                    kind="resume_case",
                    case_id=case_id,
                    resume={"decision": "approve"},
                )
            )


async def test_retry_write_back_task_runs_the_handler(sessionmaker, database_url, tenant) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[str, str]] = []

    async def write_back(tenant_id: str, promise_id: str) -> str:
        calls.append((tenant_id, promise_id))
        return "promise_recorded"

    runner = CaseRunner(graph=None, write_back=write_back)
    task = TaskMessage(tenant_id=str(tenant), kind="retry_write_back", resume={"promise_id": "p-1"})
    assert await runner.handle(task) == "promise_recorded"
    assert calls == [(str(tenant), "p-1")]
    with pytest.raises(ValueError, match="write-back handler"):
        await CaseRunner(graph=None).handle(task)

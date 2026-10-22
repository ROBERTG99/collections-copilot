"""The case graph on Postgres with the checkpointer and interrupts (FR-3, FR-6)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from langgraph.types import Command

from collections_copilot.db.repositories import Repos
from collections_copilot.integrations.sms.base import InMemorySmsSender
from collections_copilot.llm.providers.base import ProviderRequest, ProviderResponse
from collections_copilot.llm.providers.fake import FakeLLM
from tests.factories import make_account
from tests.integration.graph_support import (
    approve_latest,
    case_graph,
    config,
    make_services,
    new_case,
)

pytestmark = pytest.mark.integration

PLAN_NOTE = "Owner asked to split the balance into installments; cash flow is tight this quarter."


class WorkerKilledError(RuntimeError):
    pass


class CrashOnDraft(FakeLLM):
    """Simulates the worker dying while drafting (not a provider error, so nothing catches it)."""

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        if request.route == "draft_message":
            raise WorkerKilledError("worker killed")
        return await super().complete(request)


def nba_calls(llm: FakeLLM) -> int:
    return sum(1 for r in llm.requests if r.route == "next_best_action")


async def _status(sm: Any, tenant: uuid.UUID, case_id: str) -> str:
    async with sm() as s:
        case = await Repos(s).cases.get(tenant, case_id)
        assert case is not None
        return case.status


async def test_resume_after_worker_restart(sessionmaker, database_url, tenant) -> None:  # type: ignore[no-untyped-def]
    account = await make_account(
        sessionmaker,
        tenant,
        notes=[PLAN_NOTE],
        invoices=[{"amount_cents": 1_250_000, "days_past_due": 95}],
    )
    case_id = await new_case(sessionmaker, tenant, account)
    initial = {"case_id": case_id, "tenant_id": str(tenant), "run_id": "r1"}

    # Worker 1 dies while drafting, after load/prioritize/precheck/NBA were checkpointed.
    crashing = CrashOnDraft()
    async with case_graph(
        make_services(sessionmaker, database_url, crashing), database_url
    ) as graph:
        with pytest.raises(WorkerKilledError):
            await graph.ainvoke(initial, config(case_id))
    assert nba_calls(crashing) == 1

    # Worker 2 (new process, new checkpointer pool) resumes from the checkpoint.
    llm2 = FakeLLM()
    sms = InMemorySmsSender()
    async with case_graph(
        make_services(sessionmaker, database_url, llm2, sms), database_url
    ) as graph:
        state = await graph.aget_state(config(case_id))
        assert state.next == ("draft",)
        await graph.ainvoke(None, config(case_id))
        snapshot = await graph.aget_state(config(case_id))
        assert snapshot.next == ("human_approval",)
        assert nba_calls(llm2) == 0  # the NBA was not recomputed
        assert snapshot.values["nba"]["action"] == "payment_plan"
        assert await _status(sessionmaker, tenant, case_id) == "awaiting_approval"
        assert sms.sent == []  # nothing goes out before a human approves

    # Worker 3 resumes the interrupt after the approval API recorded the decision.
    version = await approve_latest(sessionmaker, tenant, case_id)
    async with case_graph(
        make_services(sessionmaker, database_url, FakeLLM(), sms), database_url
    ) as graph:
        await graph.ainvoke(
            Command(resume={"decision": "approve", "actor": "ana.analyst"}), config(case_id)
        )
        snapshot = await graph.aget_state(config(case_id))
        assert snapshot.next == ("await_reply",), (
            snapshot.values.get("approval"),
            snapshot.values.get("postcheck"),
            snapshot.values.get("draft"),
        )
    assert len(sms.sent) == 1
    assert sms.sent[0]["key"] == f"sms:{case_id}:{version}"
    assert await _status(sessionmaker, tenant, case_id) == "awaiting_reply"
    async with sessionmaker() as s:
        repos = Repos(s)
        [message] = await repos.messages.for_account(tenant, account)
        assert message.status == "queued"
        assert message.provider_sid == sms.sent[0]["sid"]
        follow = await repos.follow_ups.pending_for_case(tenant, uuid.UUID(case_id), "after_send")
        assert follow is not None


async def test_dispute_note_closes_without_draft(sessionmaker, database_url, tenant) -> None:  # type: ignore[no-untyped-def]
    account = await make_account(
        sessionmaker, tenant, notes=["Customer disputes invoice INV-1: damaged goods."]
    )
    case_id = await new_case(sessionmaker, tenant, account)
    async with case_graph(make_services(sessionmaker, database_url), database_url) as graph:
        result = await graph.ainvoke(
            {"case_id": case_id, "tenant_id": str(tenant)}, config(case_id)
        )
    assert result["nba"]["action"] == "pause"
    assert await _status(sessionmaker, tenant, case_id) == "closed"
    async with sessionmaker() as s:
        assert await Repos(s).drafts.for_case(tenant, uuid.UUID(case_id)) == []


async def test_small_remind_is_auto_approved_and_sent(sessionmaker, database_url, tenant) -> None:  # type: ignore[no-untyped-def]
    note = "AP contact confirmed the invoice was received and is in the approval queue."
    account = await make_account(
        sessionmaker, tenant, notes=[note], invoices=[{"amount_cents": 30_000, "days_past_due": 20}]
    )
    case_id = await new_case(sessionmaker, tenant, account)
    sms = InMemorySmsSender()

    class Confident(FakeLLM):
        async def complete(self, request: ProviderRequest) -> ProviderResponse:
            if request.route == "next_best_action":
                body = '{"action": "remind", "rationale": "Routine reminder.", "confidence": 0.95, "cited_note_ids": []}'
                return ProviderResponse(body, "end_turn", "fake")
            return await super().complete(request)

    async with case_graph(
        make_services(sessionmaker, database_url, Confident(), sms), database_url
    ) as graph:
        await graph.ainvoke({"case_id": case_id, "tenant_id": str(tenant)}, config(case_id))
    assert len(sms.sent) == 1
    async with sessionmaker() as s:
        approvals = await Repos(s).approvals.for_case(tenant, uuid.UUID(case_id))
    assert [a.decision for a in approvals] == ["auto_approve"]


async def test_reject_closes_without_send(sessionmaker, database_url, tenant) -> None:  # type: ignore[no-untyped-def]
    account = await make_account(sessionmaker, tenant, notes=[PLAN_NOTE])
    case_id = await new_case(sessionmaker, tenant, account)
    sms = InMemorySmsSender()
    async with case_graph(
        make_services(sessionmaker, database_url, sms=sms), database_url
    ) as graph:
        await graph.ainvoke({"case_id": case_id, "tenant_id": str(tenant)}, config(case_id))
        await graph.ainvoke(
            Command(resume={"decision": "reject", "reason": "tone", "actor": "ana"}),
            config(case_id),
        )
    assert sms.sent == []
    assert await _status(sessionmaker, tenant, case_id) == "closed"


async def test_runner_is_idempotent_under_redelivery(sessionmaker, database_url, tenant) -> None:  # type: ignore[no-untyped-def]
    from collections_copilot.integrations.queue.messages import TaskMessage
    from collections_copilot.worker.runner import CaseRunner

    account = await make_account(sessionmaker, tenant, notes=[PLAN_NOTE])
    case_id = await new_case(sessionmaker, tenant, account)
    llm = FakeLLM()
    sms = InMemorySmsSender()
    async with case_graph(
        make_services(sessionmaker, database_url, llm, sms), database_url
    ) as graph:
        runner = CaseRunner(graph)
        task = TaskMessage(tenant_id=str(tenant), kind="process_case", case_id=case_id)
        assert await runner.handle(task) == "started"
        assert await runner.handle(task) == "duplicate"
        assert nba_calls(llm) == 1
        early = TaskMessage(
            tenant_id=str(tenant),
            kind="resume_case",
            case_id=case_id,
            resume={"kind": "await_reply", "event": "reply"},
        )
        assert await runner.handle(early) == "kind_mismatch"
        other = TaskMessage(tenant_id=str(uuid.uuid4()), kind="process_case", case_id=case_id)
        with pytest.raises(PermissionError):
            await runner.handle(other)
        await approve_latest(sessionmaker, tenant, case_id)
        resume = TaskMessage(
            tenant_id=str(tenant),
            kind="resume_case",
            case_id=case_id,
            resume={"kind": "approval", "decision": "approve", "actor": "ana"},
        )
        assert await runner.handle(resume) == "resumed"
        assert await runner.handle(resume) == "kind_mismatch"  # now paused at await_reply
    assert len(sms.sent) == 1


async def test_redelivered_resume_finishes_a_run_that_crashed_mid_send(
    sessionmaker, database_url, tenant
) -> None:  # type: ignore[no-untyped-def]
    """A crash inside send_sms after the resume left the checkpoint at send_sms
    with no interrupt; the redelivered resume was acknowledged as `not_paused` and the case
    stayed open for ever. It must finish the run instead."""
    from collections_copilot.integrations.queue.messages import TaskMessage
    from collections_copilot.worker.runner import CaseRunner

    class CrashOnce(InMemorySmsSender):
        crashed = False

        async def send(self, to_e164: str, body: str, *, idempotency_key: str) -> Any:
            if not self.crashed:
                self.crashed = True
                raise RuntimeError("worker killed after the claim")
            return await super().send(to_e164, body, idempotency_key=idempotency_key)

    account = await make_account(sessionmaker, tenant, notes=[PLAN_NOTE])
    case_id = await new_case(sessionmaker, tenant, account)
    sms = CrashOnce()
    async with case_graph(
        make_services(sessionmaker, database_url, FakeLLM(), sms), database_url
    ) as graph:
        runner = CaseRunner(graph)
        await runner.handle(
            TaskMessage(tenant_id=str(tenant), kind="process_case", case_id=case_id)
        )
        await approve_latest(sessionmaker, tenant, case_id)
        resume = TaskMessage(
            tenant_id=str(tenant),
            kind="resume_case",
            case_id=case_id,
            resume={"kind": "approval", "decision": "approve", "actor": "ana"},
        )
        with pytest.raises(RuntimeError):
            await runner.handle(resume)
        assert await runner.handle(resume) == "recovered"  # the same SQS message, redelivered
        snapshot = await graph.aget_state(config(case_id))
    # The claimed message was in flight when the worker died: it is never sent twice blindly,
    # it is marked failed and a retry is scheduled, and the run does not stay at send_sms.
    assert snapshot.next != ("send_sms",)
    assert snapshot.values["send"] == {"status": "failed", "reason": "unknown_after_restart"}
    assert sms.sent == []
    async with sessionmaker() as s:
        retry = await Repos(s).follow_ups.pending_for_case(tenant, uuid.UUID(case_id), "send_retry")
    assert retry is not None


async def test_crash_after_the_auto_approval_is_stored_still_sends_once(
    sessionmaker, database_url, tenant
) -> None:  # type: ignore[no-untyped-def]
    """The auto-approval row was committed, then the worker died
    before the checkpoint. The re-run found the draft no longer pending and paused for a human,
    who could not decide it (409 already_decided). The stored auto-approval is the decision."""
    from collections_copilot.integrations.queue.messages import TaskMessage
    from collections_copilot.worker.runner import CaseRunner

    note = "AP contact confirmed the invoice was received and is in the approval queue."
    account = await make_account(
        sessionmaker, tenant, notes=[note], invoices=[{"amount_cents": 30_000, "days_past_due": 20}]
    )
    case_id = await new_case(sessionmaker, tenant, account)
    sms = InMemorySmsSender()

    class Confident(FakeLLM):
        async def complete(self, request: ProviderRequest) -> ProviderResponse:
            if request.route == "next_best_action":
                body = '{"action": "remind", "rationale": "Routine reminder.", "confidence": 0.95, "cited_note_ids": []}'
                return ProviderResponse(body, "end_turn", "fake")
            return await super().complete(request)

    services = make_services(sessionmaker, database_url, Confident(), sms)
    original = services.store.record_auto_approval
    crashed: list[bool] = []

    async def record_then_die(*args: Any, **kwargs: Any) -> None:
        await original(*args, **kwargs)
        if not crashed:
            crashed.append(True)
            raise RuntimeError("worker killed after the auto-approval commit")

    services.store.record_auto_approval = record_then_die  # type: ignore[method-assign]
    async with case_graph(services, database_url) as graph:
        runner = CaseRunner(graph)
        task = TaskMessage(tenant_id=str(tenant), kind="process_case", case_id=case_id)
        with pytest.raises(RuntimeError):
            await runner.handle(task)
        assert await runner.handle(task) == "recovered"  # the same SQS message, redelivered
        snapshot = await graph.aget_state(config(case_id))
    # Sent, now waiting for the debtor; never left waiting for a human who cannot decide.
    assert [i.value["kind"] for i in snapshot.interrupts] == ["await_reply"]
    assert len(sms.sent) == 1
    async with sessionmaker() as s:
        approvals = await Repos(s).approvals.for_case(tenant, uuid.UUID(case_id))
    assert [a.decision for a in approvals] == ["auto_approve"]

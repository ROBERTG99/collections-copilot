"""T-30 / FR-10: follow-ups become work under a single scheduler leader."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import pytest

from collections_copilot.agent.scheduler import Scheduler
from collections_copilot.db.repositories import Repos
from collections_copilot.db.session import LeaderLock
from collections_copilot.integrations.email.smtp import InMemoryNotifier
from collections_copilot.integrations.queue.messages import TaskMessage
from tests.factories import make_account
from tests.integration.graph_support import FIXED_NOW

pytestmark = pytest.mark.integration


class Sink:
    def __init__(self) -> None:
        self.tasks: list[TaskMessage] = []

    async def __call__(self, task: TaskMessage) -> None:
        self.tasks.append(task)

    def for_tenant(self, tenant: uuid.UUID) -> list[TaskMessage]:
        return [t for t in self.tasks if t.tenant_id == str(tenant)]


def scheduler(sm: Any, sink: Sink, key: str, notifier: InMemoryNotifier | None = None) -> Scheduler:
    engine = sm.kw["bind"]
    return Scheduler(
        sm, LeaderLock(engine, key), sink, notifier or InMemoryNotifier(), clock=lambda: FIXED_NOW
    )


async def _promise(
    sm: Any, tenant: uuid.UUID, invoice_status: str, due_days_ago: int = 1
) -> tuple[uuid.UUID, uuid.UUID]:
    number = f"INV-{uuid.uuid4().hex[:8]}"
    account = await make_account(
        sm, tenant, invoices=[{"amount_cents": 90_000, "number": number, "status": invoice_status}]
    )
    async with sm() as s, s.begin():
        repos = Repos(s)
        old = await repos.cases.create(tenant, account, status="closed")
        promise = await repos.promises.create_if_absent(
            tenant,
            account_id=account,
            case_id=old.id,
            amount_cents=90_000,
            currency="USD",
            promised_date=FIXED_NOW.date() - timedelta(days=due_days_ago),
            invoice_numbers=[number],
            status="recorded",
            idempotency_key=f"ptp:{tenant}:SM{uuid.uuid4().hex}",
        )
        await repos.follow_ups.add(
            tenant,
            case_id=old.id,
            account_id=account,
            promise_id=promise.id,
            kind="promise_due",
            due_at=FIXED_NOW - timedelta(hours=1),
            payload={"promise_id": str(promise.id)},
        )
        return account, promise.id


async def test_broken_promise_reopens_case(sessionmaker, tenant) -> None:  # type: ignore[no-untyped-def]
    account, promise_id = await _promise(sessionmaker, tenant, "open")
    sink = Sink()
    sched = scheduler(sessionmaker, sink, f"test-{uuid.uuid4()}")
    try:
        first = await sched.tick()
        second = await sched.tick()
    finally:
        await sched.lock.release()
    assert first.leader
    async with sessionmaker() as s:
        repos = Repos(s)
        promise = await repos.promises.get(tenant, promise_id)
        new_case = await repos.cases.open_for_account(tenant, account)
    assert promise is not None
    assert promise.status == "broken"
    assert new_case is not None
    assert new_case.status == "queued"
    mine = sink.for_tenant(tenant)
    assert [(t.kind, t.case_id) for t in mine] == [("process_case", str(new_case.id))]
    assert second.leader
    assert len(sink.for_tenant(tenant)) == 1  # the follow-up is done: no second case


async def test_paid_promise_is_kept(sessionmaker, tenant) -> None:  # type: ignore[no-untyped-def]
    account, promise_id = await _promise(sessionmaker, tenant, "paid")
    sink = Sink()
    sched = scheduler(sessionmaker, sink, f"test-{uuid.uuid4()}")
    try:
        await sched.tick()
    finally:
        await sched.lock.release()
    async with sessionmaker() as s:
        repos = Repos(s)
        promise = await repos.promises.get(tenant, promise_id)
        assert await repos.cases.open_for_account(tenant, account) is None
    assert promise is not None
    assert promise.status == "kept"
    assert sink.for_tenant(tenant) == []


async def test_single_leader(sessionmaker, tenant) -> None:  # type: ignore[no-untyped-def]
    account = await make_account(sessionmaker, tenant)
    async with sessionmaker() as s, s.begin():
        repos = Repos(s)
        for _ in range(10):
            case = await repos.cases.create(tenant, account, status="closed")
            await repos.follow_ups.add(
                tenant,
                case_id=case.id,
                account_id=account,
                kind="deferred_send",
                due_at=FIXED_NOW - timedelta(minutes=5),
            )
    key = f"test-{uuid.uuid4()}"
    sink = Sink()
    worker_a, worker_b = scheduler(sessionmaker, sink, key), scheduler(sessionmaker, sink, key)
    try:
        assert await worker_a.lock.try_acquire()
        assert not await worker_b.lock.try_acquire()
        follower = await worker_b.tick()
        assert not follower.leader
        assert sink.for_tenant(tenant) == []
        # Even two schedulers that both believe they lead process each row once.
        await asyncio.gather(
            worker_a.tick(require_leader=False), worker_b.tick(require_leader=False)
        )
        mine = sink.for_tenant(tenant)
        assert len(mine) == 10
        assert len({t.case_id for t in mine}) == 10
        assert all(t.resume == {"kind": "deferred_send"} for t in mine)
        # Leadership moves when the leader goes away.
        await worker_a.lock.release()
        assert await worker_b.lock.try_acquire()
    finally:
        await worker_a.lock.release()
        await worker_b.lock.release()


async def test_reminders_only_while_still_waiting(sessionmaker, tenant) -> None:  # type: ignore[no-untyped-def]
    account = await make_account(sessionmaker, tenant)
    async with sessionmaker() as s, s.begin():
        repos = Repos(s)
        waiting = await repos.cases.create(tenant, account, status="awaiting_approval")
        for case_id, kind in ((waiting.id, "approval_reminder"), (waiting.id, "send_retry")):
            await repos.follow_ups.add(
                tenant,
                case_id=case_id,
                account_id=account,
                kind=kind,
                due_at=FIXED_NOW - timedelta(minutes=1),
            )
    notifier = InMemoryNotifier()
    sched = scheduler(sessionmaker, Sink(), f"test-{uuid.uuid4()}", notifier)
    try:
        await sched.tick()
        await sched.tick()
    finally:
        await sched.lock.release()
    mine = [n for n in notifier.sent if n["case_id"] == str(waiting.id)]
    assert sorted(n["key"].split(":")[0] for n in mine) == ["approval-reminder", "send_retry"]

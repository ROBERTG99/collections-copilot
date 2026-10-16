"""Follow-up scheduler (FR-10): one leader across workers turns due follow-ups into work.

Leadership is a Postgres advisory lock held on a dedicated connection (`LeaderLock`). Each tick
claims due rows with `FOR UPDATE SKIP LOCKED`, acts on them and marks them done in the same
transaction, so a crash leaves them pending for the next tick, and two schedulers that both
think they lead still process each row once. Enqueued work is idempotent in the runner.

  deferred_send        → resume the case at its `deferred_send` interrupt
  after_send           → no reply within 3 business days: resume `await_reply` with `no_reply`
  promise_due          → invoices still open: promise `broken`, a new case is opened and queued;
                         otherwise `kept`
  approval_reminder    → still awaiting approval after 24 h: email the supervisor
  send_retry           → an SMS outcome is unknown: email the supervisor (never resent blindly)
  payment_verification → the debtor says paid: email the supervisor to verify the remittance
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collections_copilot.db import audit
from collections_copilot.db.models import FollowUp
from collections_copilot.db.repositories import Repos
from collections_copilot.db.session import LeaderLock
from collections_copilot.integrations.email.smtp import Notifier
from collections_copilot.integrations.queue.messages import TaskMessage

log = logging.getLogger(__name__)
Enqueue = Callable[[TaskMessage], Awaitable[None]]


@dataclass
class TickResult:
    leader: bool
    done: dict[str, int] = field(default_factory=dict)
    enqueued: list[TaskMessage] = field(default_factory=list)


class Scheduler:
    def __init__(
        self,
        sm: async_sessionmaker[AsyncSession],
        lock: LeaderLock,
        enqueue: Enqueue,
        notifier: Notifier,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        batch: int = 50,
    ) -> None:
        self.sm = sm
        self.lock = lock
        self.enqueue = enqueue
        self.notifier = notifier
        self.clock = clock
        self.batch = batch

    async def tick(self, *, require_leader: bool = True) -> TickResult:
        if require_leader and not await self.lock.try_acquire():
            return TickResult(leader=False)
        result = TickResult(leader=True)
        now = self.clock()
        for _ in range(self.batch):
            # One row per transaction: a queue failure rolls back only that row, which stays
            # pending for the next tick; tasks already sent belong to committed rows.
            async with self.sm() as s, s.begin():
                repos = Repos(s)
                rows = await repos.follow_ups.claim_due(now, limit=1)
                if not rows:
                    break
                row = rows[0]
                tasks = await self._act(repos, row, now)
                for task in tasks:
                    await self.enqueue(task)
                row.status = "done"
            result.enqueued.extend(tasks)
            result.done[row.kind] = result.done.get(row.kind, 0) + 1
        return result

    async def run(self, interval_s: float, stop: asyncio.Event) -> None:
        try:
            while not stop.is_set():
                try:
                    await self.tick()
                except Exception:
                    log.exception("scheduler tick failed")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=interval_s)
        finally:
            await self.lock.release()

    # --- actions --------------------------------------------------------------------------
    async def _act(self, repos: Repos, row: FollowUp, now: datetime) -> list[TaskMessage]:
        tenant = row.tenant_id
        case = await repos.cases.get(tenant, row.case_id) if row.case_id else None
        if row.kind == "deferred_send":
            return [self._resume(row, {"kind": "deferred_send"})] if case else []
        if row.kind == "after_send":
            if case is None or case.status != "awaiting_reply":
                return []
            return [self._resume(row, {"kind": "await_reply", "event": "no_reply"})]
        if row.kind == "promise_due":
            return await self._promise_due(repos, row, now)
        if row.kind == "approval_reminder":
            if case is not None and case.status == "awaiting_approval":
                await self.notifier.notify_supervisor(
                    case_id=str(case.id),
                    reason="a draft has waited more than 24 h for approval",
                    idempotency_key=f"approval-reminder:{row.id}",
                )
            return []
        if row.kind in ("send_retry", "payment_verification"):
            reason = (
                "an SMS outcome is unknown; check the provider log before contacting again"
                if row.kind == "send_retry"
                else "the debtor reports a payment; verify the remittance"
            )
            await self.notifier.notify_supervisor(
                case_id=str(row.case_id or row.account_id),
                reason=reason,
                idempotency_key=f"{row.kind}:{row.id}",
            )
            return []
        log.warning("unknown follow-up kind %s", row.kind)
        return []

    def _resume(self, row: FollowUp, resume: dict[str, object]) -> TaskMessage:
        return TaskMessage(
            tenant_id=str(row.tenant_id),
            kind="resume_case",
            case_id=str(row.case_id),
            resume=dict(resume),
        )

    async def _promise_due(self, repos: Repos, row: FollowUp, now: datetime) -> list[TaskMessage]:
        tenant = row.tenant_id
        promise_id = row.promise_id or uuid.UUID(str(row.payload.get("promise_id")))
        promise = await repos.promises.get_for_update(tenant, promise_id)
        if promise is None or promise.status != "recorded":
            return []
        invoices = await repos.invoices.for_account(tenant, promise.account_id)
        named = [i for i in invoices if i.number in promise.invoice_numbers]
        still_open = [i for i in (named or invoices) if i.status == "open"]
        if not still_open:
            promise.status = "kept"
            await self._audit(repos, tenant, "promise.kept", promise.id, {})
            return []
        promise.status = "broken"
        await self._audit(repos, tenant, "promise.broken", promise.id, {"open": len(still_open)})
        await repos.queue.refresh_scores(tenant, now.date(), [promise.account_id])
        if await repos.cases.open_for_account(tenant, promise.account_id) is not None:
            return []  # an open case already covers this account
        account = await repos.accounts.get(tenant, promise.account_id)
        async with repos.session.begin_nested():
            case = await repos.cases.create(
                tenant,
                promise.account_id,
                status="queued",
                priority_score=account.priority_score if account else None,
                created_by="scheduler:broken_promise",
            )
        await self._audit(
            repos, tenant, "case.created", case.id, {"reason": "broken_promise"}, entity="case"
        )
        return [TaskMessage(tenant_id=str(tenant), kind="process_case", case_id=str(case.id))]

    async def _audit(
        self,
        repos: Repos,
        tenant: uuid.UUID,
        action: str,
        entity_id: uuid.UUID,
        payload: dict[str, object],
        entity: str = "promise",
    ) -> None:
        await audit.append(
            repos.session,
            tenant,
            actor_type="system",
            actor_id="scheduler",
            action=action,
            entity=entity,
            entity_id=str(entity_id),
            payload=payload,
        )

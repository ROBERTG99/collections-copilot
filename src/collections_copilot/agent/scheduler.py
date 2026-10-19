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
  promise_write_back   → the ledger was unavailable: a `retry_write_back` task, same key
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collections_copilot.db import audit
from collections_copilot.db.models import FollowUp
from collections_copilot.db.repositories import Repos
from collections_copilot.db.session import LeaderLock
from collections_copilot.integrations.email.smtp import Notifier
from collections_copilot.integrations.queue.messages import ReplyMessage, TaskMessage

log = logging.getLogger(__name__)
Enqueue = Callable[[TaskMessage], Awaitable[None]]
EnqueueReply = Callable[[ReplyMessage], Awaitable[None]]
MAX_ATTEMPTS = 5
BACKOFF = timedelta(minutes=5)
SWEEP_GRACE = timedelta(minutes=2)
SCORE_REFRESH_EVERY_S = 24 * 3600


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
        enqueue_reply: EnqueueReply | None = None,
    ) -> None:
        self.sm = sm
        self.lock = lock
        self.enqueue = enqueue
        self.enqueue_reply = enqueue_reply
        self.notifier = notifier
        self.clock = clock
        self.batch = batch
        self._last_score_refresh = float("-inf")

    async def tick(self, *, require_leader: bool = True) -> TickResult:
        if require_leader and not await self.lock.try_acquire():
            return TickResult(leader=False)
        result = TickResult(leader=True)
        now = self.clock()
        for _ in range(self.batch):
            row_ref: tuple[uuid.UUID, uuid.UUID, str, dict[str, Any]] | None = None
            try:
                async with self.sm() as s, s.begin():
                    repos = Repos(s)
                    rows = await repos.follow_ups.claim_due(now, limit=1)
                    if not rows:
                        break
                    row = rows[0]
                    row_ref = (row.id, row.tenant_id, row.kind, dict(row.payload or {}))
                    tasks = await self._act(repos, row, now)
                    row.status = "done"
            except Exception as exc:  # one bad row must never stall every other follow-up
                if row_ref is None:
                    raise
                await self._back_off(row_ref, exc, now)
                continue
            # Enqueue only after the commit: a task never points at uncommitted rows. A lost
            # task is recovered by the sweeps below (queued cases, received replies).
            for task in tasks:
                try:
                    await self.enqueue(task)
                    result.enqueued.append(task)
                except Exception:
                    log.warning("follow-up task not enqueued", extra={"kind": task.kind})
            result.done[row_ref[2]] = result.done.get(row_ref[2], 0) + 1
        await self._sweep(result)
        await self._daily_score_refresh(now)
        return result

    async def _back_off(
        self,
        row_ref: tuple[uuid.UUID, uuid.UUID, str, dict[str, Any]],
        exc: Exception,
        now: datetime,
    ) -> None:
        row_id, tenant, kind, payload = row_ref
        attempts = int(payload.get("attempts", 0)) + 1
        log.warning("follow-up failed", extra={"kind": kind, "attempts": attempts})
        async with self.sm() as s, s.begin():
            repos = Repos(s)
            if attempts >= MAX_ATTEMPTS:
                await repos.follow_ups.set(tenant, row_id, status="cancelled")
                await audit.append(
                    s,
                    tenant,
                    actor_type="system",
                    actor_id="scheduler",
                    action="follow_up.failed",
                    entity="follow_up",
                    entity_id=str(row_id),
                    payload={"kind": kind, "attempts": attempts, "error": type(exc).__name__},
                )
                return
            await repos.follow_ups.set(
                tenant,
                row_id,
                status="pending",
                due_at=now + BACKOFF * 2 ** (attempts - 1),
                payload={**payload, "attempts": attempts},
            )

    async def _sweep(self, result: TickResult) -> None:
        """Recover work whose enqueue was lost (queue outage, crash after a commit): cases still
        `queued` and replies still `received` after a grace period. Both are idempotent."""
        cutoff = datetime.now(UTC) - SWEEP_GRACE
        async with self.sm() as s:
            repos = Repos(s)
            cases = await repos.cases.stale_queued(cutoff, limit=self.batch)
            replies = await repos.messages.stale_received(cutoff, limit=self.batch)
        for case in cases:
            task = TaskMessage(
                tenant_id=str(case.tenant_id), kind="process_case", case_id=str(case.id)
            )
            with contextlib.suppress(Exception):
                await self.enqueue(task)
                result.enqueued.append(task)
        if self.enqueue_reply is None:
            return
        for msg in replies:
            reply = ReplyMessage(
                tenant_id=str(msg.tenant_id),
                message_id=str(msg.id),
                provider_sid=str(msg.provider_sid),
                from_e164=str(msg.from_e164 or ""),
                body=msg.body,
                received_at=msg.created_at,
            )
            with contextlib.suppress(Exception):
                await self.enqueue_reply(reply)

    async def _daily_score_refresh(self, now: datetime) -> None:
        """Days past due grow every day; cached queue scores are refreshed once a day."""
        if time.monotonic() - self._last_score_refresh < SCORE_REFRESH_EVERY_S:
            return
        self._last_score_refresh = time.monotonic()
        async with self.sm() as s:
            tenants = await Repos(s).tenants.list_ids()
        for tenant in tenants:
            async with self.sm() as s, s.begin():
                await Repos(s).queue.refresh_scores(tenant, now.date())

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
        if row.kind == "promise_write_back":
            promise = await repos.promises.get(tenant, row.promise_id) if row.promise_id else None
            if promise is None or promise.status not in ("pending", "approval_required"):
                return []
            return [
                TaskMessage(
                    tenant_id=str(tenant),
                    kind="retry_write_back",
                    resume={"promise_id": str(promise.id)},
                )
            ]
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
        if account is None or account.opted_out_at is not None or account.contact_flag:
            return []  # nobody to contact: the broken promise is recorded, no case is opened
        if await repos.opt_outs.is_opted_out(tenant, account.phone_e164):
            return []
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

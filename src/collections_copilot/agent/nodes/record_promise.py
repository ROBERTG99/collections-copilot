"""Promise write-back (FR-9) through mcp-gateway with `ptp:<tenant>:<MessageSid>`.

The promises row (unique idempotency_key) is created before the ledger call, so a redelivered
reply reuses it, and the gateway replays the stored ledger result for the same key: one ledger
row. `approval_required` holds the promise for a supervisor; the retry sends the same key plus
`_meta.approval_id`. `-32040 idempotency_conflict` marks the promise failed with no retry.
A transient failure (gateway down, `-32047` still in progress) leaves the promise pending and
raises `LedgerUnavailableError`, so SQS redelivers the task and the same key is tried again.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collections_copilot.agent.services import Services
from collections_copilot.db import audit
from collections_copilot.db.models import Promise
from collections_copilot.db.repositories import Repos
from collections_copilot.integrations.mcp.ledger import LedgerResult, LedgerTools

FINAL = {"recorded", "failed", "rejected", "kept", "broken"}


class LedgerUnavailableError(RuntimeError):
    """The write-back may not have happened yet; retry with the same key (T-51 review M2)."""


OPEN = ("pending", "approval_required")


def promise_key(tenant_id: str, provider_sid: str) -> str:
    return f"ptp:{tenant_id}:{provider_sid}"


def _ledger_args(promise: Promise, account_ref: str, provider_sid: str) -> dict[str, Any]:
    return {
        "account_ref": account_ref,
        "amount_cents": int(promise.amount_cents),
        "currency": promise.currency,
        "promised_date": promise.promised_date.isoformat(),
        "invoice_numbers": list(promise.invoice_numbers),
        "source_message_sid": provider_sid,
    }


async def _apply(
    services: Services,
    sm: async_sessionmaker[AsyncSession],
    tenant: uuid.UUID,
    promise_id: uuid.UUID,
    account_id: uuid.UUID,
    case_id: uuid.UUID | None,
    result: LedgerResult,
    actor: str,
) -> str:
    async with sm() as s, s.begin():
        repos = Repos(s)
        # Serialise concurrent writers (a redelivery racing a retry) on the promise row; only a
        # promise still waiting on the ledger moves on, so a final state is never overwritten and
        # the promise_due follow-up and audit row are written once.
        promise = await repos.promises.get_for_update(tenant, promise_id)
        assert promise is not None
        if promise.status not in OPEN:
            return f"promise_{promise.status}"
        if result.status == "unavailable":  # the promise stays open; the redelivery retries
            raise LedgerUnavailableError(result.detail or "ledger unavailable")
        if result.status == "approval_required" and promise.status == "approval_required":
            return "promise_held"
        if result.status == "recorded":
            await repos.promises.set(
                tenant,
                promise_id,
                status="recorded",
                ledger_ref=result.ledger_ref,
                failure_reason=None,
            )
            due = datetime.combine(
                promise.promised_date + timedelta(days=1),
                time(14, 0),
                tzinfo=services.clock().tzinfo,
            )
            await repos.follow_ups.add(
                tenant,
                case_id=case_id,
                account_id=account_id,
                promise_id=promise_id,
                kind="promise_due",
                due_at=due,
                payload={"promise_id": str(promise_id)},
            )
            outcome, action = "promise_recorded", "promise.recorded"
        elif result.status == "approval_required":
            await repos.promises.set(
                tenant, promise_id, status="approval_required", mcp_approval_id=result.approval_id
            )
            outcome, action = "promise_held", "promise.approval_required"
        elif result.status == "idempotency_conflict":
            await repos.promises.set(
                tenant, promise_id, status="failed", failure_reason="idempotency_conflict"
            )
            outcome, action = "promise_failed", "promise.idempotency_conflict"
        else:
            await repos.promises.set(
                tenant,
                promise_id,
                status="failed",
                failure_reason=(result.status if result.status != "error" else "ledger_error"),
            )
            outcome, action = "promise_failed", f"promise.{result.status}"
        await audit.append(
            s,
            tenant,
            actor_type="system",
            actor_id=actor,
            action=action,
            entity="promise",
            entity_id=str(promise_id),
            payload={
                "ledger_ref": result.ledger_ref,
                "approval_id": result.approval_id,
                "reasons": result.reasons,
                "detail": result.detail,
                "replayed": result.replayed,
            },
        )
    if outcome == "promise_failed":
        await services.notifier.notify_supervisor(
            case_id=str(case_id or promise_id),
            reason=f"promise write-back failed: {result.status}",
            idempotency_key=f"ptp-failed:{promise_id}",
        )
    return outcome


async def record_promise(
    services: Services, sm: async_sessionmaker[AsyncSession], state: dict[str, Any] | Any
) -> dict[str, Any]:
    reply, acc, promise_in = state["reply"], state["account"], state["promise"]
    tenant = uuid.UUID(reply["tenant_id"])
    key = promise_key(reply["tenant_id"], reply["provider_sid"])
    case_id = uuid.UUID(state["case_id"]) if state.get("case_id") else None
    account_id = uuid.UUID(acc["id"])
    async with sm() as s, s.begin():
        repos = Repos(s)
        promise = await repos.promises.create_if_absent(
            tenant,
            account_id=account_id,
            case_id=case_id,
            message_id=uuid.UUID(reply["message_id"]),
            amount_cents=int(promise_in["amount_cents"]),
            currency=acc["currency"],
            promised_date=date.fromisoformat(promise_in["promised_date"]),
            invoice_numbers=list(acc["open_invoices"]) or ["UNSPECIFIED"],
            status="pending",
            idempotency_key=key,
        )
        promise_id, status = promise.id, promise.status
        args = _ledger_args(promise, acc["external_ref"], reply["provider_sid"])
    if status in FINAL or status == "approval_required":
        return {"outcome": f"promise_{status}", "promise_id": str(promise_id)}
    ledger: LedgerTools | None = services.ledger  # type: ignore[assignment]
    if ledger is None:
        result = LedgerResult("unavailable", detail="no ledger configured")
    else:
        result = await ledger.record_promise_to_pay(reply["tenant_id"], args, idempotency_key=key)
    outcome = await _apply(
        services, sm, tenant, promise_id, account_id, case_id, result, "reply-graph"
    )
    return {"outcome": outcome, "promise_id": str(promise_id)}


async def retry_write_back(
    services: Services, sm: async_sessionmaker[AsyncSession], tenant_id: str, promise_id: str
) -> str:
    """After a supervisor approved the held write at the gateway: same key + approval_id."""
    tenant = uuid.UUID(tenant_id)
    async with sm() as s:
        repos = Repos(s)
        promise = await repos.promises.get(tenant, promise_id)
        if promise is None:
            raise LookupError("promise not found")
        if promise.status != "approval_required" or not promise.mcp_approval_id:
            return f"promise_{promise.status}"
        account = await repos.accounts.get(tenant, promise.account_id)
        message = (
            await repos.messages.get(tenant, promise.message_id) if promise.message_id else None
        )
        assert account is not None
        sid = (
            message.provider_sid
            if message and message.provider_sid
            else promise.idempotency_key.rsplit(":", 1)[-1]
        )
        args = _ledger_args(promise, account.external_ref, sid)
        key, approval_id, account_id, case_id = (
            promise.idempotency_key,
            promise.mcp_approval_id,
            account.id,
            promise.case_id,
        )
    ledger: LedgerTools | None = services.ledger  # type: ignore[assignment]
    if ledger is None:
        result = LedgerResult("unavailable", detail="no ledger configured")
    else:
        result = await ledger.record_promise_to_pay(
            tenant_id, args, idempotency_key=key, approval_id=approval_id
        )
    return await _apply(
        services,
        sm,
        tenant,
        uuid.UUID(str(promise_id)),
        account_id,
        case_id,
        result,
        "retry-write-back",
    )

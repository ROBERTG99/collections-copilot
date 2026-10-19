"""Promises, replies and follow-ups (FR-8, FR-9, FR-10), and supervisor decisions on promises
the gateway held for approval.

Approve: the decision is made at mcp-gateway first, then recorded here and committed, then a
`retry_write_back` task replays the same ledger call with `_meta.approval_id` and the same
idempotency key. A retry of the request is safe: an approval already granted at the gateway is
read back and the task is enqueued again (the write-back is idempotent).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from collections_copilot.api import views
from collections_copilot.api.deps.auth import ANALYST_OR_SUPERVISOR, ANY_ROLE, SUPERVISOR, Principal
from collections_copilot.api.deps.db import REPOS
from collections_copilot.api.errors import ProblemError
from collections_copilot.api.schemas import MessageOut, PromiseOut
from collections_copilot.db import audit
from collections_copilot.db.models import Promise
from collections_copilot.db.repositories import Repos
from collections_copilot.integrations.mcp.admin import GatewayAdminError
from collections_copilot.integrations.queue.messages import TaskMessage
from collections_copilot.integrations.queue.sqs import QueueUnavailableError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["promises"])
PromiseStatus = Literal[
    "pending", "approval_required", "recorded", "failed", "kept", "broken", "rejected"
]


class RejectBody(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class FollowUpOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    due_at: datetime
    case_id: uuid.UUID | None
    account_id: uuid.UUID
    promise_id: uuid.UUID | None


class PromiseDecisionOut(BaseModel):
    promise: PromiseOut
    gateway_status: str


@router.get("/promises", response_model=list[PromiseOut])
async def list_promises(
    status: PromiseStatus | None = None,
    limit: int = Query(100, ge=1, le=500),
    principal: Principal = Depends(ANY_ROLE),
    repos: Repos = REPOS,
) -> list[PromiseOut]:
    rows = await repos.promises.list(principal.tenant_id, status=status, limit=limit)
    accounts = await repos.accounts.get_many(principal.tenant_id, (p.account_id for p in rows))
    return [views.promise_out(p, accounts.get(p.account_id)) for p in rows]


@router.get("/replies", response_model=list[MessageOut])
async def list_replies(
    limit: int = Query(100, ge=1, le=500),
    principal: Principal = Depends(ANY_ROLE),
    repos: Repos = REPOS,
) -> list[MessageOut]:
    rows = await repos.messages.inbound(principal.tenant_id, limit)
    accounts = await repos.accounts.get_many(principal.tenant_id, (m.account_id for m in rows))
    return [
        views.message_out(m, accounts.get(m.account_id) if m.account_id else None) for m in rows
    ]


@router.get("/follow-ups", response_model=list[FollowUpOut])
async def list_follow_ups(
    status: Literal["pending", "claimed", "done", "cancelled"] | None = None,
    limit: int = Query(100, ge=1, le=500),
    principal: Principal = Depends(ANY_ROLE),
    repos: Repos = REPOS,
) -> list[FollowUpOut]:
    rows = await repos.follow_ups.list(principal.tenant_id, status=status, limit=limit)
    return [
        FollowUpOut(
            id=f.id,
            kind=f.kind,
            status=f.status,
            due_at=f.due_at,
            case_id=f.case_id,
            account_id=f.account_id,
            promise_id=f.promise_id,
        )
        for f in rows
    ]


async def _held(repos: Repos, principal: Principal, promise_id: uuid.UUID) -> Promise:
    promise = await repos.promises.get_for_update(principal.tenant_id, promise_id)
    if promise is None:
        raise ProblemError(404, "promise not found", code="not_found")
    if not promise.mcp_approval_id or promise.status not in ("approval_required", "pending"):
        raise ProblemError(
            409, f"the promise is {promise.status}, not awaiting approval", code="not_held"
        )
    return promise


@router.post(
    "/promises/{promise_id}:approve",
    response_model=PromiseDecisionOut,
    responses={404: {}, 409: {}, 503: {}},
)
async def approve_promise(
    promise_id: uuid.UUID, request: Request, principal: Principal = Depends(SUPERVISOR)
) -> PromiseDecisionOut:
    app = request.app
    tenant = principal.tenant_id
    async with app.state.sessionmaker() as s, s.begin():
        repos = Repos(s)
        promise = await _held(repos, principal, promise_id)
        invoices = await repos.invoices.for_account(tenant, promise.account_id)
        if not any(i.status == "open" for i in invoices):
            # Paid or disputed since the reply: do not write a promise for nothing owed.
            raise ProblemError(
                409, "the account has no open invoices any more", code="nothing_open"
            )
        approval_id = str(promise.mcp_approval_id)
        gateway_status = await _gateway_decide(
            request, str(tenant), approval_id, "approve", f"approved by {principal.username}"
        )
        promise.decided_by = principal.username
        await audit.append(
            s,
            tenant,
            actor_type="user",
            actor_id=principal.username,
            action="promise.approve",
            entity="promise",
            entity_id=str(promise.id),
            payload={"approval_id": approval_id, "gateway_status": gateway_status},
        )
        out = PromiseDecisionOut(promise=views.promise_out(promise), gateway_status=gateway_status)
    task = TaskMessage(
        tenant_id=str(tenant), kind="retry_write_back", resume={"promise_id": str(promise_id)}
    )
    try:
        await app.state.queues.send(
            app.state.settings.sqs_tasks_queue, task.model_dump(mode="json", by_alias=True)
        )
    except QueueUnavailableError as exc:
        raise ProblemError(
            503,
            "approved at the gateway, but the write-back could not be queued; retry",
            code="queue_unavailable",
            headers={"Retry-After": "5"},
        ) from exc
    return out


@router.post(
    "/promises/{promise_id}:reject", response_model=PromiseDecisionOut, responses={404: {}, 409: {}}
)
async def reject_promise(
    promise_id: uuid.UUID,
    body: RejectBody,
    request: Request,
    principal: Principal = Depends(SUPERVISOR),
) -> PromiseDecisionOut:
    tenant = principal.tenant_id
    async with request.app.state.sessionmaker() as s, s.begin():
        repos = Repos(s)
        promise = await _held(repos, principal, promise_id)
        gateway_status = await _gateway_decide(
            request, str(tenant), str(promise.mcp_approval_id), "reject", body.reason
        )
        promise.status = "rejected"
        promise.failure_reason = "rejected_by_supervisor"
        promise.decided_by = principal.username
        await audit.append(
            s,
            tenant,
            actor_type="user",
            actor_id=principal.username,
            action="promise.reject",
            entity="promise",
            entity_id=str(promise.id),
            payload={"approval_id": promise.mcp_approval_id, "reason": body.reason},
        )
        await s.flush()
        return PromiseDecisionOut(promise=views.promise_out(promise), gateway_status=gateway_status)


async def _gateway_decide(
    request: Request,
    tenant: str,
    approval_id: str,
    decision: Literal["approve", "reject"],
    note: str,
) -> str:
    admin = request.app.state.gateway_admin
    try:
        result: dict[str, Any] = await admin.decide(tenant, approval_id, decision, note)
        return str(result.get("status"))
    except GatewayAdminError as exc:
        if exc.status == 409:  # already decided (a retried request): read the decision back
            current = await admin.get(tenant, approval_id)
            wanted = "approved" if decision == "approve" else "rejected"
            if current.get("status") == wanted:
                return wanted
            raise ProblemError(
                409, f"the gateway approval is {current.get('status')}", code="gateway_conflict"
            ) from exc
        if exc.status == 410:
            raise ProblemError(
                409, "the gateway approval expired", code="approval_expired"
            ) from exc
        if exc.status == 404:
            raise ProblemError(404, "unknown gateway approval", code="not_found") from exc
        raise ProblemError(
            503,
            "the gateway is unavailable",
            code="gateway_unavailable",
            headers={"Retry-After": "5"},
        ) from exc


class ReclassifyBody(BaseModel):
    label: Literal["promise_to_pay", "dispute", "wrong_number", "paid", "stop", "other"]
    note: str | None = Field(default=None, max_length=500)


@router.post(
    "/replies/{message_id}:reclassify", response_model=MessageOut, responses={404: {}, 409: {}}
)
async def reclassify_reply(
    message_id: uuid.UUID,
    body: ReclassifyBody,
    principal: Principal = Depends(ANALYST_OR_SUPERVISOR),
    repos: Repos = REPOS,
) -> MessageOut:
    """An analyst corrects the label of a processed reply. The correction is stored next to the
    model's label (which the reply graph keeps reading) for review and evals; it never replays
    side effects (a STOP is applied by recording an opt-out, not by relabelling)."""
    tenant = principal.tenant_id
    message = await repos.messages.get(tenant, message_id)
    if message is None or message.direction != "inbound":
        raise ProblemError(404, "reply not found", code="not_found")
    if body.label == "stop":
        raise ProblemError(
            409, "record an opt-out instead of relabelling a reply as STOP", code="use_opt_out"
        )
    if message.status != "handled":
        raise ProblemError(
            409, "the reply has not been processed yet; correct it afterwards", code="not_processed"
        )
    extracted = dict(message.extracted or {})
    previous = (extracted.get("correction") or {}).get("label") or message.classification
    extracted["correction"] = {
        "label": body.label,
        "by": principal.username,
        "at": datetime.now(UTC).isoformat(),
        "note": body.note,
    }
    message.extracted = extracted
    await audit.append(
        repos.session,
        tenant,
        actor_type="user",
        actor_id=principal.username,
        action="reply.reclassified",
        entity="message",
        entity_id=str(message.id),
        payload={"from": previous, "to": body.label, "note": body.note},
    )
    await repos.session.flush()
    account = await repos.accounts.get(tenant, message.account_id) if message.account_id else None
    return views.message_out(message, account)

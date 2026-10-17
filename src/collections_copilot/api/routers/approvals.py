"""Human approval (FR-6, NFR-5): approve, edit (diff stored) or reject a draft, single or batch.

The API is where a human decision becomes an approval record. It locks the case, checks that the
decision is for the latest draft version of a case awaiting approval, re-runs the compliance
post-check on the exact text (the edited text for an edit) with facts read now, stores the
approval and the audit event, commits, and only then enqueues `resume_case` for the worker.
The worker re-checks again before sending; nothing is sent without this record.

A retry of the same decision (for example after a 503 from the queue) is answered from the
stored record and re-enqueues the resume, which the runner applies at most once.
"""

from __future__ import annotations

import difflib
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, model_validator

from collections_copilot.agent.store import load_facts
from collections_copilot.api import views
from collections_copilot.api.deps.auth import ANALYST_OR_SUPERVISOR, Principal
from collections_copilot.api.errors import ProblemError
from collections_copilot.api.schemas import CaseSummary
from collections_copilot.compliance import engine
from collections_copilot.db import audit
from collections_copilot.db.models import Draft
from collections_copilot.db.repositories import Repos
from collections_copilot.integrations.queue.messages import TaskMessage
from collections_copilot.integrations.queue.sqs import QueueUnavailableError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["approvals"])
MAX_BODY = 1000
MAX_BATCH = 50


class ApprovalRequest(BaseModel):
    decision: Literal["approve", "edit", "reject"]
    draft_version: int = Field(ge=1, le=1_000_000)
    body: str | None = Field(default=None, max_length=MAX_BODY)
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _fields_for_decision(self) -> ApprovalRequest:
        if self.decision == "edit" and not (self.body or "").strip():
            raise ValueError("an edit needs the edited body")
        if self.decision == "reject" and not (self.reason or "").strip():
            raise ValueError("a rejection needs a reason")
        if self.decision != "edit" and self.body is not None:
            raise ValueError("body is only accepted with decision=edit")
        return self


class ApprovalResult(BaseModel):
    case: CaseSummary
    decision: str
    draft_version: int
    postcheck: dict[str, Any] | None
    diff: str | None = None
    replayed: bool = False


class BatchItem(ApprovalRequest):
    case_id: uuid.UUID


class BatchRequest(BaseModel):
    items: list[BatchItem] = Field(min_length=1, max_length=MAX_BATCH)


class BatchItemResult(BaseModel):
    case_id: uuid.UUID
    status: int
    result: ApprovalResult | None = None
    error: dict[str, Any] | None = None


class BatchResponse(BaseModel):
    results: list[BatchItemResult]


def unified_diff(before: str, after: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            before.splitlines(), after.splitlines(), "draft", "edit", lineterm="", n=1
        )
    )


async def _postcheck(
    repos: Repos,
    tenant: uuid.UUID,
    case_id: uuid.UUID,
    account_id: uuid.UUID,
    action: str | None,
    body: str,
    now: datetime,
) -> engine.CheckResult:
    facts = await load_facts(repos, tenant, case_id, account_id, now)
    return engine.postcheck(
        engine.ComplianceContext(
            now=now,
            account=engine.AccountFacts(
                facts.timezone, facts.language, facts.currency, facts.opted_out
            ),
            invoices=[
                engine.InvoiceFacts(i.number, i.amount_cents, i.currency, i.status)
                for i in facts.invoices
            ],
            contacts_7d=[datetime.fromisoformat(c) for c in facts.contacts_7d],
            action=action,
            body=body,
            account_name=facts.name,
            contact_flag=facts.contact_flag,
        )
    )


async def _replay(
    repos: Repos, tenant: uuid.UUID, case_id: uuid.UUID, req: ApprovalRequest, latest: Draft
) -> tuple[Draft, str] | None:
    """The same decision was already stored (a client retry): return (draft, decision)."""
    approval = await repos.approvals.for_draft(tenant, latest.id)
    if approval is None:
        return None
    if req.decision == "edit":
        same = (
            approval.decision == "edit"
            and latest.version == req.draft_version + 1
            and latest.body.strip() == (req.body or "").strip()
        )
    else:
        same = approval.decision == req.decision and latest.version == req.draft_version
    return (latest, approval.decision) if same else None


async def decide(
    request: Request, principal: Principal, case_id: uuid.UUID, req: ApprovalRequest
) -> ApprovalResult:
    app = request.app
    tenant = principal.tenant_id
    now = datetime.now(UTC)
    async with app.state.sessionmaker() as session, session.begin():
        repos = Repos(session)
        case = await repos.cases.get_for_update(tenant, case_id)
        if case is None:
            raise ProblemError(404, "case not found", code="not_found")
        latest = await repos.drafts.latest(tenant, case.id)
        if latest is None:
            raise ProblemError(409, "the case has no draft", code="no_draft")
        replay = await _replay(repos, tenant, case.id, req, latest)
        if replay is not None:
            draft, decision = replay
            result = ApprovalResult(
                case=views.case_summary(case),
                decision=decision,
                draft_version=draft.version,
                postcheck=draft.postcheck,
                replayed=True,
            )
        else:
            result = await _record(repos, principal, case, latest, req, now)
    await _enqueue_resume(request, principal, case_id, result, req)
    return result


async def _record(
    repos: Repos,
    principal: Principal,
    case: Any,
    latest: Draft,
    req: ApprovalRequest,
    now: datetime,
) -> ApprovalResult:
    tenant = principal.tenant_id
    if case.status != "awaiting_approval":
        raise ProblemError(
            409, f"the case is {case.status}, not awaiting approval", code="not_awaiting_approval"
        )
    if latest.version != req.draft_version:
        raise ProblemError(
            409,
            f"draft version {req.draft_version} is stale; the latest is {latest.version}",
            code="stale_draft",
            extra={"latest_version": latest.version},
        )
    if latest.status != "pending" or await repos.approvals.for_draft(tenant, latest.id):
        raise ProblemError(409, "this draft was already decided", code="already_decided")

    if req.decision == "reject":
        await repos.approvals.add(
            tenant,
            draft_id=latest.id,
            case_id=case.id,
            decision="reject",
            reason=req.reason,
            decided_by=principal.username,
        )
        latest.status = "rejected"
        await audit.append(
            repos.session,
            tenant,
            actor_type="user",
            actor_id=principal.username,
            action="approval.reject",
            entity="case",
            entity_id=str(case.id),
            payload={"draft_version": latest.version, "reason": req.reason},
        )
        return ApprovalResult(
            case=views.case_summary(case),
            decision="reject",
            draft_version=latest.version,
            postcheck=latest.postcheck,
        )

    body = (req.body or "").strip() if req.decision == "edit" else latest.body
    check = await _postcheck(repos, tenant, case.id, case.account_id, case.nba_action, body, now)
    if not check.ok:
        # Blocking violations stop the approval; nothing is stored, the analyst edits again.
        raise ProblemError(
            422,
            "the text fails the compliance post-check",
            code="postcheck_failed",
            extra={"postcheck": check.as_dict()},
        )
    diff = None
    target = latest
    if req.decision == "edit":
        diff = unified_diff(latest.body, body)
        latest.status = "superseded"
        target = await repos.drafts.add(
            tenant,
            case.id,
            version=latest.version + 1,
            body=body,
            language=latest.language,
            author="analyst",
            prompt_version=latest.prompt_version,
            model=None,
            precheck=latest.precheck,
            postcheck=check.as_dict(),
        )
    else:
        latest.postcheck = check.as_dict()
    target.status = "approved"
    await repos.approvals.add(
        tenant,
        draft_id=target.id,
        case_id=case.id,
        decision=req.decision,
        reason=req.reason,
        diff=diff,
        decided_by=principal.username,
        # Only a deferral is stored; otherwise the worker sends when it resumes (re-checking
        # the window then).
        send_at=check.send_at if check.deferred else None,
    )
    await audit.append(
        repos.session,
        tenant,
        actor_type="user",
        actor_id=principal.username,
        action=f"approval.{req.decision}",
        entity="case",
        entity_id=str(case.id),
        payload={
            "draft_version": target.version,
            "diff": diff,
            "send_at": check.send_at.isoformat(),
            "deferred": check.deferred,
        },
    )
    await repos.session.flush()
    return ApprovalResult(
        case=views.case_summary(case),
        decision=req.decision,
        draft_version=target.version,
        postcheck=check.as_dict(),
        diff=diff,
    )


async def _enqueue_resume(
    request: Request,
    principal: Principal,
    case_id: uuid.UUID,
    result: ApprovalResult,
    req: ApprovalRequest,
) -> None:
    app = request.app
    task = TaskMessage(
        tenant_id=str(principal.tenant_id),
        kind="resume_case",
        case_id=str(case_id),
        resume={
            "kind": "approval",
            "decision": result.decision,
            "draft_version": result.draft_version,
            "reason": req.reason,
            "actor": principal.username,
        },
    )
    try:
        await app.state.queues.send(
            app.state.settings.sqs_tasks_queue, task.model_dump(mode="json", by_alias=True)
        )
    except QueueUnavailableError as exc:
        log.warning("task queue unavailable; approval stored, resume not enqueued")
        raise ProblemError(
            503,
            "the decision is stored but the worker could not be notified; retry the same request",
            code="queue_unavailable",
            headers={"Retry-After": "5"},
        ) from exc


@router.post(
    "/cases/{case_id}/approval",
    response_model=ApprovalResult,
    responses={404: {}, 409: {"description": "stale or already decided"}, 422: {}},
)
async def approve_case(
    case_id: uuid.UUID,
    body: ApprovalRequest,
    request: Request,
    principal: Principal = Depends(ANALYST_OR_SUPERVISOR),
) -> ApprovalResult:
    return await decide(request, principal, case_id, body)


@router.post("/approvals:batch", response_model=BatchResponse)
async def approve_batch(
    body: BatchRequest,
    request: Request,
    principal: Principal = Depends(ANALYST_OR_SUPERVISOR),
) -> BatchResponse:
    """Each item is its own transaction: one stale or failing item does not block the rest."""
    results = []
    for item in body.items:
        try:
            result = await decide(request, principal, item.case_id, item)
            results.append(BatchItemResult(case_id=item.case_id, status=200, result=result))
        except ProblemError as exc:
            results.append(
                BatchItemResult(
                    case_id=item.case_id,
                    status=exc.status,
                    error={"code": exc.code, "detail": exc.detail, **exc.extra},
                )
            )
    return BatchResponse(results=results)

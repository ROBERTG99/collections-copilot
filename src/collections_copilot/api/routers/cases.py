"""Cases (FR-4): create cases for selected accounts and read a case with its drafts and steps.

POST commits the case rows first and enqueues `process_case` after the commit, so the worker never
sees a case that does not exist. If the queue is down the call returns 503; a retry finds the
still-`queued` cases and enqueues them again (the runner ignores duplicate deliveries).
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import IntegrityError

from collections_copilot.api import views
from collections_copilot.api.deps.auth import ANALYST_OR_SUPERVISOR, ANY_ROLE, Principal
from collections_copilot.api.deps.db import REPOS
from collections_copilot.api.errors import ProblemError
from collections_copilot.api.routers.queue import queue_item
from collections_copilot.api.schemas import CaseDetail, CreateCases, CreateCasesOut, CreatedCase
from collections_copilot.db import audit
from collections_copilot.db.models import Case
from collections_copilot.db.repositories import Repos
from collections_copilot.integrations.queue.messages import TaskMessage
from collections_copilot.integrations.queue.sqs import QueueUnavailableError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["cases"])


async def _open_or_create(
    repos: Repos, principal: Principal, account_id: uuid.UUID
) -> tuple[Case, bool]:
    tenant = principal.tenant_id
    existing = await repos.cases.open_for_account(tenant, account_id)
    if existing is not None:
        return existing, True
    account = await repos.accounts.get(tenant, account_id)
    try:
        async with repos.session.begin_nested():
            case = await repos.cases.create(
                tenant,
                account_id,
                status="queued",
                priority_score=account.priority_score if account else None,
                score_components=account.score_components if account else None,
                created_by=principal.username,
            )
    except IntegrityError:  # a concurrent request opened a case for this account first
        winner = await repos.cases.open_for_account(tenant, account_id)
        if winner is None:
            raise
        return winner, True
    return case, False


@router.post(
    "/cases",
    status_code=202,
    response_model=CreateCasesOut,
    responses={404: {"description": "unknown account"}, 409: {"description": "all exist"}},
)
async def create_cases(
    body: CreateCases,
    request: Request,
    principal: Principal = Depends(ANALYST_OR_SUPERVISOR),
) -> CreateCasesOut:
    app = request.app
    tenant = principal.tenant_id
    account_ids = list(dict.fromkeys(body.account_ids))
    created: list[tuple[Case, bool]] = []
    async with app.state.sessionmaker() as session, session.begin():
        repos = Repos(session)
        for account_id in account_ids:
            if await repos.accounts.get(tenant, account_id) is None:
                raise ProblemError(404, f"account {account_id} not found", code="account_not_found")
        for account_id in account_ids:
            created.append(await _open_or_create(repos, principal, account_id))
        new_ids = [str(c.id) for c, existing in created if not existing]
        if new_ids:
            await audit.append(
                session,
                tenant,
                actor_type="user",
                actor_id=principal.username,
                action="case.created",
                entity="case",
                entity_id=new_ids[0] if len(new_ids) == 1 else None,
                payload={"case_ids": new_ids},
            )
    if all(existing for _, existing in created) and not any(
        c.status == "queued" for c, _ in created
    ):
        raise ProblemError(
            409,
            "every selected account already has an open case",
            code="case_exists",
            extra={"case_ids": [str(c.id) for c, _ in created]},
        )
    try:
        for case, _ in created:
            if case.status == "queued":
                task = TaskMessage(tenant_id=str(tenant), kind="process_case", case_id=str(case.id))
                await app.state.queues.send(
                    app.state.settings.sqs_tasks_queue,
                    task.model_dump(mode="json", by_alias=True),
                )
    except QueueUnavailableError as exc:
        log.warning("task queue unavailable; cases stay queued")
        raise ProblemError(
            503,
            "the task queue is unavailable; retry to enqueue the queued cases",
            code="queue_unavailable",
            headers={"Retry-After": "5"},
        ) from exc
    return CreateCasesOut(
        cases=[
            CreatedCase(id=c.id, account_id=c.account_id, status=c.status, existing=existing)
            for c, existing in created
        ]
    )


@router.get("/cases/{case_id}", response_model=CaseDetail)
async def get_case(
    case_id: uuid.UUID,
    principal: Principal = Depends(ANY_ROLE),
    repos: Repos = REPOS,
) -> CaseDetail:
    tenant = principal.tenant_id
    case = await repos.cases.get(tenant, case_id)
    if case is None:
        raise ProblemError(404, "case not found", code="not_found")
    account = await repos.accounts.get(tenant, case.account_id)
    if account is None:  # pragma: no cover - foreign key
        raise ProblemError(404, "case not found", code="not_found")
    drafts = await repos.drafts.for_case(tenant, case.id)
    versions = {d.id: d.version for d in drafts}
    notes = await repos.notes.for_account(tenant, account.id, limit=200)
    cited = {str(n) for n in case.cited_note_ids or []}
    messages = [
        m for m in await repos.messages.for_account(tenant, account.id) if m.case_id == case.id
    ]
    return CaseDetail(
        case=views.case_summary(case),
        account=queue_item(account, case if case.status not in ("closed", "failed") else None),
        drafts=[views.draft_out(d) for d in drafts],
        approvals=[
            views.approval_out(a, versions) for a in await repos.approvals.for_case(tenant, case.id)
        ],
        steps=[views.step_out(s) for s in await repos.steps.for_case(tenant, case.id)],
        cited_notes=[views.note_out(n) for n in notes if str(n.id) in cited],
        messages=[views.message_out(m) for m in messages],
        precheck=case.precheck,
        recipient_e164=account.phone_e164,
    )

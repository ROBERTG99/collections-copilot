"""Account detail (FR-3): invoices with aging, notes, cases, messages, promises, live pre-check."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from collections_copilot.api import views
from collections_copilot.api.deps.auth import ANY_ROLE, Principal
from collections_copilot.api.deps.db import REPOS
from collections_copilot.api.errors import ProblemError
from collections_copilot.api.schemas import AccountDetail, Money
from collections_copilot.db.repositories import Repos

router = APIRouter(prefix="/api/v1", tags=["accounts"])

# Cases in the detail payload; the open case is always the newest (one open case per account).
ACCOUNT_HISTORY_LIMIT = 20
# Messages and promises in the detail payload, newest first (messages: MessagesRepo's default).
ACCOUNT_ITEMS_LIMIT = 50


@router.get("/accounts/{account_id}", response_model=AccountDetail)
async def get_account(
    account_id: uuid.UUID,
    principal: Principal = Depends(ANY_ROLE),
    repos: Repos = REPOS,
) -> AccountDetail:
    tenant = principal.tenant_id
    account = await repos.accounts.get(tenant, account_id)
    if account is None:  # another tenant's account is indistinguishable from a missing one
        raise ProblemError(404, "account not found", code="not_found")
    now = datetime.now(UTC)
    today = now.date()
    invoices = await repos.invoices.for_account(tenant, account.id)
    open_cents = sum(
        i.amount_cents for i in invoices if i.status == "open" and i.currency == account.currency
    )
    return AccountDetail(
        id=account.id,
        external_ref=account.external_ref,
        name=account.name,
        phone_e164=account.phone_e164,
        timezone=account.timezone,
        segment=account.segment,
        language=account.language,
        currency=account.currency,
        opted_out=account.opted_out_at is not None,
        contact_flag=account.contact_flag,
        priority_score=account.priority_score,
        score_components=account.score_components,
        open_balance=Money(amount_cents=open_cents, currency=account.currency),
        aging=views.aging(invoices, today),
        invoices=[views.invoice_out(i, today) for i in invoices],
        notes=[views.note_out(n) for n in await repos.notes.for_account(tenant, account.id)],
        cases=[
            views.case_summary(c)
            for c in await repos.cases.for_account(tenant, account.id, ACCOUNT_HISTORY_LIMIT)
        ],
        cases_total=await repos.cases.count_for_account(tenant, account.id),
        messages=[
            views.message_out(m)
            for m in await repos.messages.for_account(tenant, account.id, ACCOUNT_ITEMS_LIMIT)
        ],
        promises=[
            views.promise_out(p)
            for p in await repos.promises.for_account(tenant, account.id, ACCOUNT_ITEMS_LIMIT)
        ],
        precheck=await views.live_precheck(repos, account, invoices, now),
    )

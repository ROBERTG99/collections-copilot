"""Opt-outs (FR-5, FR-8): the tenant's do-not-text list. A manual opt-out (for example a debtor
who asked by phone) cancels pending drafts and closes open cases like STOP, without an SMS."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from collections_copilot.agent.reply_actions import cancel_open_work_in
from collections_copilot.api.deps.auth import ANY_ROLE, Principal
from collections_copilot.api.deps.db import REPOS
from collections_copilot.db import audit
from collections_copilot.db.repositories import Repos

router = APIRouter(prefix="/api/v1", tags=["opt-outs"])


class OptOutIn(BaseModel):
    phone_e164: str = Field(pattern=r"^\+[1-9]\d{7,14}$")
    reason: str | None = Field(default=None, max_length=200)


class OptOutOut(BaseModel):
    phone_e164: str
    source: str
    created_by: str | None
    created_at: datetime


class OptOutResult(BaseModel):
    opt_out: OptOutOut
    created: bool
    cases_closed: int


@router.get("/opt-outs", response_model=list[OptOutOut])
async def list_opt_outs(
    limit: int = Query(200, ge=1, le=1000),
    principal: Principal = Depends(ANY_ROLE),
    repos: Repos = REPOS,
) -> list[OptOutOut]:
    rows = await repos.opt_outs.list(principal.tenant_id, limit)
    return [
        OptOutOut(
            phone_e164=r.phone_e164,
            source=r.source,
            created_by=r.created_by,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.post("/opt-outs", response_model=OptOutResult, status_code=201)
async def add_opt_out(
    body: OptOutIn,
    response: Response,
    # Analysts record a debtor's spoken request; admins own compliance settings (spec §3).
    principal: Principal = Depends(ANY_ROLE),
    repos: Repos = REPOS,
) -> OptOutResult:
    tenant, now = principal.tenant_id, datetime.now(UTC)
    created = await repos.opt_outs.add(tenant, body.phone_e164, "analyst", principal.username)
    await repos.accounts.set_opted_out(tenant, body.phone_e164, now)
    closed = 0
    for account in await repos.accounts.get_by_phone(tenant, body.phone_e164):
        closed += len(await cancel_open_work_in(repos, tenant, account.id, now, "opt_out"))
    await audit.append(
        repos.session,
        tenant,
        actor_type="user",
        actor_id=principal.username,
        action="opt_out.created" if created else "opt_out.repeated",
        entity="opt_out",
        entity_id=None,
        payload={"source": "analyst", "reason": body.reason},
    )
    row = await repos.opt_outs.get(tenant, body.phone_e164)
    assert row is not None
    response.status_code = 201 if created else 200
    return OptOutResult(
        opt_out=OptOutOut(
            phone_e164=row.phone_e164,
            source=row.source,
            created_by=row.created_by,
            created_at=row.created_at,
        ),
        created=created,
        cases_closed=closed,
    )

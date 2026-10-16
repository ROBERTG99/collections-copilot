"""Audit log (FR-12): the tenant's hash-chained events, newest first, with an optional verify."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from collections_copilot.api.deps.auth import SUPERVISOR, Principal
from collections_copilot.api.deps.db import REPOS
from collections_copilot.db import audit
from collections_copilot.db.repositories import Repos

router = APIRouter(prefix="/api/v1", tags=["audit"])


class AuditEventOut(BaseModel):
    seq: int
    ts: datetime
    actor_type: str
    actor_id: str
    action: str
    entity: str
    entity_id: str | None
    payload: dict[str, Any]
    hash: str
    prev_hash: str


class AuditPage(BaseModel):
    items: list[AuditEventOut]
    next_before_seq: int | None
    chain_ok: bool | None = None
    first_bad_seq: int | None = None


@router.get("/audit", response_model=AuditPage)
async def list_audit(
    limit: int = Query(100, ge=1, le=500),
    before_seq: int | None = Query(None, ge=1, le=2**63 - 1),
    action: str | None = Query(None, max_length=64, pattern=r"^[a-z_.]+$"),
    entity_id: str | None = Query(None, max_length=64),
    verify: bool = False,
    principal: Principal = Depends(SUPERVISOR),
    repos: Repos = REPOS,
) -> AuditPage:
    rows = await repos.metrics.audit_page(
        principal.tenant_id,
        limit=limit + 1,
        before_seq=before_seq,
        action=action,
        entity_id=entity_id,
    )
    page = AuditPage(
        items=[
            AuditEventOut(
                seq=r.seq,
                ts=r.ts,
                actor_type=r.actor_type,
                actor_id=r.actor_id,
                action=r.action,
                entity=r.entity,
                entity_id=r.entity_id,
                payload=r.payload_redacted,
                hash=r.hash,
                prev_hash=r.prev_hash,
            )
            for r in rows[:limit]
        ],
        next_before_seq=rows[limit - 1].seq if len(rows) > limit else None,
    )
    if verify:
        bad = await audit.verify_tenant(repos.session, principal.tenant_id)
        page.chain_ok, page.first_bad_seq = bad is None, bad
    return page

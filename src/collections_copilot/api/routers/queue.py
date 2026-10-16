"""Prioritized queue (FR-2): accounts by deterministic priority score, keyset-paginated."""

from __future__ import annotations

import base64
import binascii
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from collections_copilot.api.deps.auth import ANY_ROLE, Principal
from collections_copilot.api.deps.db import REPOS
from collections_copilot.api.errors import ProblemError
from collections_copilot.api.schemas import Money, QueueItem, QueuePage
from collections_copilot.db.models import Account, Case
from collections_copilot.db.repositories import Repos

router = APIRouter(prefix="/api/v1", tags=["queue"])
Segment = Literal["enterprise", "mid_market", "smb"]


def encode_cursor(score: int, account_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{score}:{account_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[int, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        score, account_id = raw.split(":", 1)
        return int(score), uuid.UUID(account_id)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ProblemError(400, "invalid cursor", code="invalid_cursor") from exc


def queue_item(account: Account, case: Case | None) -> QueueItem:
    precheck: dict[str, Any] = (case.precheck if case is not None else None) or {}
    violations = precheck.get("violations") or []
    return QueueItem(
        account_id=account.id,
        external_ref=account.external_ref,
        name=account.name,
        segment=account.segment,
        language=account.language,
        priority_score=account.priority_score,
        score_components=account.score_components,
        open_balance=Money(amount_cents=account.open_balance_cents, currency=account.currency),
        max_days_past_due=account.max_days_past_due,
        opted_out=account.opted_out_at is not None,
        contact_flag=account.contact_flag,
        case_id=case.id if case else None,
        case_status=case.status if case else None,
        nba_action=case.nba_action if case else None,
        nba_confidence=case.nba_confidence if case else None,
        fallback=bool(case and case.fallback),
        compliance_ok=bool(precheck["ok"]) if "ok" in precheck else None,
        compliance_codes=[str(v.get("code")) for v in violations if isinstance(v, dict)],
    )


@router.get("/queue", response_model=QueuePage)
async def get_queue(
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None, max_length=128),
    segment: Segment | None = None,
    min_score: int | None = Query(None, ge=0, le=100),
    status: str | None = Query(None, max_length=32, pattern=r"^[a-z_]+$"),
    q: str | None = Query(None, max_length=100),
    principal: Principal = Depends(ANY_ROLE),
    repos: Repos = REPOS,
) -> QueuePage:
    rows = await repos.queue.page(
        principal.tenant_id,
        limit=limit + 1,
        after=decode_cursor(cursor) if cursor else None,
        segment=segment,
        min_score=min_score,
        status=status,
        search=q.strip() if q else None,
    )
    items = [queue_item(a, c) for a, c in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1][0]
        next_cursor = encode_cursor(last.priority_score, last.id)
    return QueuePage(items=items, next_cursor=next_cursor)

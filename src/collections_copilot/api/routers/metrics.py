"""Ops metrics (FR-14): every card on the console's Ops page comes from this one response."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from collections_copilot.api.deps.auth import SUPERVISOR, Principal
from collections_copilot.api.deps.db import REPOS
from collections_copilot.compliance.rules.window import in_window
from collections_copilot.db.repositories import Repos

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/metrics", tags=["metrics"])


class OpsMetrics(BaseModel):
    generated_at: datetime
    window_days: int
    cases_by_status: dict[str, int]
    pending_approvals: int
    promises_by_status: dict[str, int]
    sends: int
    contact_window_rate: float | None  # share of sends inside 08:00–21:00 debtor local time
    llm_cost_usd: float
    accounts_with_cases: int
    cost_per_account_usd: float | None
    node_p95_ms: dict[str, float]
    dlq_depth: dict[str, int | None]
    opt_outs: int
    follow_ups_pending: int
    replies: int


@router.get("/ops", response_model=OpsMetrics)
async def ops_metrics(
    request: Request,
    window_days: int = Query(30, ge=1, le=365),
    principal: Principal = Depends(SUPERVISOR),
    repos: Repos = REPOS,
) -> OpsMetrics:
    tenant = principal.tenant_id
    now = datetime.now(UTC)
    since = now - timedelta(days=window_days)
    by_status = await repos.metrics.cases_by_status(tenant)
    sends = await repos.metrics.sends_since(tenant, since)
    inside = sum(1 for sent_at, tz in sends if in_window(sent_at, tz))
    cost, accounts = await repos.metrics.llm_cost(tenant, since)
    counts = await repos.metrics.counts(tenant)
    settings = request.app.state.settings
    dlq: dict[str, int | None] = {}
    for queue in (settings.sqs_tasks_dlq, settings.sqs_replies_dlq):
        try:
            dlq[queue] = await request.app.state.queues.depth(queue)
        except Exception:  # the dashboard shows "unknown" rather than failing
            log.warning("queue depth unavailable", extra={"queue": queue})
            dlq[queue] = None
    return OpsMetrics(
        generated_at=now,
        window_days=window_days,
        cases_by_status=by_status,
        pending_approvals=by_status.get("awaiting_approval", 0),
        promises_by_status=await repos.metrics.promises_by_status(tenant),
        sends=len(sends),
        contact_window_rate=round(inside / len(sends), 4) if sends else None,
        llm_cost_usd=round(cost, 6),
        accounts_with_cases=accounts,
        cost_per_account_usd=round(cost / accounts, 6) if accounts else None,
        node_p95_ms=await repos.metrics.node_p95_ms(tenant, since),
        dlq_depth=dlq,
        **counts,
    )

"""Ops aggregates (FR-14), tenant-scoped, computed in SQL."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import distinct, func, select

from collections_copilot.db.models import (
    Account,
    AuditEvent,
    Case,
    FollowUp,
    Message,
    OptOut,
    Promise,
    StepRecordRow,
)
from collections_copilot.db.repositories.base import Repo, TenantId


class MetricsRepo(Repo):
    async def cases_by_status(self, tenant_id: TenantId) -> dict[str, int]:
        rows = await self.s.execute(
            select(Case.status, func.count())
            .where(Case.tenant_id == tenant_id)
            .group_by(Case.status)
        )
        return {status: int(n) for status, n in rows.all()}

    async def promises_by_status(self, tenant_id: TenantId) -> dict[str, int]:
        rows = await self.s.execute(
            select(Promise.status, func.count())
            .where(Promise.tenant_id == tenant_id)
            .group_by(Promise.status)
        )
        return {status: int(n) for status, n in rows.all()}

    async def sends_since(self, tenant_id: TenantId, since: datetime) -> list[tuple[datetime, str]]:
        """(sent_at, debtor timezone) for outbound SMS actually handed to the provider."""
        rows = await self.s.execute(
            select(Message.sent_at, Account.timezone)
            .join(Account, Account.id == Message.account_id)
            .where(
                Message.tenant_id == tenant_id,
                Message.direction == "outbound",
                Message.sent_at.is_not(None),
                Message.sent_at >= since,
            )
        )
        return [(sent_at, tz) for sent_at, tz in rows.all() if sent_at is not None]

    async def llm_cost(self, tenant_id: TenantId, since: datetime) -> tuple[float, int]:
        """(model cost in USD, accounts with a case) over the window."""
        cost = await self.s.execute(
            select(func.coalesce(func.sum(StepRecordRow.cost_usd), 0)).where(
                StepRecordRow.tenant_id == tenant_id,
                StepRecordRow.kind == "llm",
                StepRecordRow.started_at >= since,
            )
        )
        accounts = await self.s.execute(
            select(func.count(distinct(Case.account_id))).where(
                Case.tenant_id == tenant_id, Case.created_at >= since
            )
        )
        return float(cost.scalar_one()), int(accounts.scalar_one())

    async def node_p95_ms(self, tenant_id: TenantId, since: datetime) -> dict[str, float]:
        p95 = func.percentile_cont(0.95).within_group(StepRecordRow.latency_ms)
        rows = await self.s.execute(
            select(StepRecordRow.node, p95)
            .where(
                StepRecordRow.tenant_id == tenant_id,
                StepRecordRow.kind == "node",
                StepRecordRow.status != "interrupted",
                StepRecordRow.started_at >= since,
            )
            .group_by(StepRecordRow.node)
        )
        return {node: round(float(v), 1) for node, v in rows.all()}

    async def counts(self, tenant_id: TenantId) -> dict[str, int]:
        async def count(model: Any, *where: Any) -> int:
            stmt = select(func.count()).select_from(model).where(*where)
            return int((await self.s.execute(stmt)).scalar_one())

        return {
            "opt_outs": await count(OptOut, OptOut.tenant_id == tenant_id),
            "follow_ups_pending": await count(
                FollowUp, FollowUp.tenant_id == tenant_id, FollowUp.status == "pending"
            ),
            "replies": await count(
                Message, Message.tenant_id == tenant_id, Message.direction == "inbound"
            ),
        }

    async def audit_page(
        self,
        tenant_id: uuid.UUID,
        *,
        limit: int,
        before_seq: int | None = None,
        action: str | None = None,
        entity_id: str | None = None,
    ) -> list[AuditEvent]:
        stmt = select(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
        if before_seq is not None:
            stmt = stmt.where(AuditEvent.seq < before_seq)
        if action:
            stmt = stmt.where(AuditEvent.action == action)
        if entity_id:
            stmt = stmt.where(AuditEvent.entity_id == entity_id)
        stmt = stmt.order_by(AuditEvent.seq.desc()).limit(limit)
        return list((await self.s.execute(stmt)).scalars())

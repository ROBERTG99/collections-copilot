from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update

from collections_copilot.db.models import OPEN_CASE_PREDICATE, Approval, Case, Draft
from collections_copilot.db.repositories.base import Repo, TenantId, as_uuid

OPEN_STATUSES = (
    "queued",
    "processing",
    "awaiting_approval",
    "scheduled",
    "sent",
    "awaiting_reply",
    "requires_analyst",
    "escalated",
)
assert "closed" in OPEN_CASE_PREDICATE


class CasesRepo(Repo):
    async def create(self, tenant_id: TenantId, account_id: uuid.UUID, **fields: Any) -> Case:
        case = Case(tenant_id=tenant_id, account_id=account_id, **fields)
        self.s.add(case)
        await self.s.flush()
        return case

    async def get(self, tenant_id: TenantId, case_id: uuid.UUID | str) -> Case | None:
        cid = as_uuid(case_id)
        if cid is None:
            return None
        stmt = select(Case).where(Case.tenant_id == tenant_id, Case.id == cid)
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def get_for_update(self, tenant_id: TenantId, case_id: uuid.UUID) -> Case | None:
        stmt = select(Case).where(Case.tenant_id == tenant_id, Case.id == case_id).with_for_update()
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def open_for_account(self, tenant_id: TenantId, account_id: uuid.UUID) -> Case | None:
        stmt = select(Case).where(
            Case.tenant_id == tenant_id,
            Case.account_id == account_id,
            Case.status.in_(OPEN_STATUSES),
        )
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def for_account(self, tenant_id: TenantId, account_id: uuid.UUID) -> list[Case]:
        stmt = (
            select(Case)
            .where(Case.tenant_id == tenant_id, Case.account_id == account_id)
            .order_by(Case.created_at.desc())
        )
        return list((await self.s.execute(stmt)).scalars())

    async def set(self, tenant_id: TenantId, case_id: uuid.UUID, **fields: Any) -> None:
        await self.s.execute(
            update(Case).where(Case.tenant_id == tenant_id, Case.id == case_id).values(**fields)
        )


class DraftsRepo(Repo):
    async def add(self, tenant_id: TenantId, case_id: uuid.UUID, **fields: Any) -> Draft:
        draft = Draft(tenant_id=tenant_id, case_id=case_id, **fields)
        self.s.add(draft)
        await self.s.flush()
        return draft

    async def for_case(self, tenant_id: TenantId, case_id: uuid.UUID) -> list[Draft]:
        stmt = (
            select(Draft)
            .where(Draft.tenant_id == tenant_id, Draft.case_id == case_id)
            .order_by(Draft.version)
        )
        return list((await self.s.execute(stmt)).scalars())

    async def latest(self, tenant_id: TenantId, case_id: uuid.UUID) -> Draft | None:
        drafts = await self.for_case(tenant_id, case_id)
        return drafts[-1] if drafts else None

    async def get_version(
        self, tenant_id: TenantId, case_id: uuid.UUID, version: int
    ) -> Draft | None:
        stmt = select(Draft).where(
            Draft.tenant_id == tenant_id, Draft.case_id == case_id, Draft.version == version
        )
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def cancel_pending_for_account(self, tenant_id: TenantId, account_id: uuid.UUID) -> int:
        case_ids = select(Case.id).where(Case.tenant_id == tenant_id, Case.account_id == account_id)
        result = await self.s.execute(
            update(Draft)
            .where(
                Draft.tenant_id == tenant_id, Draft.case_id.in_(case_ids), Draft.status == "pending"
            )
            .values(status="cancelled")
        )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]


class ApprovalsRepo(Repo):
    async def add(self, tenant_id: TenantId, **fields: Any) -> Approval:
        approval = Approval(tenant_id=tenant_id, **fields)
        self.s.add(approval)
        await self.s.flush()
        return approval

    async def for_draft(self, tenant_id: TenantId, draft_id: uuid.UUID) -> Approval | None:
        stmt = select(Approval).where(
            Approval.tenant_id == tenant_id, Approval.draft_id == draft_id
        )
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def for_case(self, tenant_id: TenantId, case_id: uuid.UUID) -> list[Approval]:
        stmt = (
            select(Approval)
            .where(Approval.tenant_id == tenant_id, Approval.case_id == case_id)
            .order_by(Approval.decided_at)
        )
        return list((await self.s.execute(stmt)).scalars())

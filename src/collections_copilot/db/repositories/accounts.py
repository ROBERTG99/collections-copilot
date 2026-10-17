from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from collections_copilot.db.models import Account, Case, Invoice, Note, Promise
from collections_copilot.db.repositories.base import Repo, TenantId, as_uuid
from collections_copilot.db.repositories.cases import OPEN_STATUSES


class AccountsRepo(Repo):
    async def get(self, tenant_id: TenantId, account_id: uuid.UUID | str) -> Account | None:
        aid = as_uuid(account_id)
        if aid is None:
            return None
        stmt = select(Account).where(Account.tenant_id == tenant_id, Account.id == aid)
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def get_by_ref(self, tenant_id: TenantId, external_ref: str) -> Account | None:
        stmt = select(Account).where(
            Account.tenant_id == tenant_id, Account.external_ref == external_ref
        )
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def get_by_phone(self, tenant_id: TenantId, phone_e164: str) -> list[Account]:
        stmt = select(Account).where(
            Account.tenant_id == tenant_id, Account.phone_e164 == phone_e164
        )
        return list((await self.s.execute(stmt)).scalars())

    async def tenants_with_phone(self, phone_e164: str) -> list[uuid.UUID]:
        """Webhook STOP handling (cross-tenant by design): every tenant holding this number."""
        stmt = select(Account.tenant_id).where(Account.phone_e164 == phone_e164).distinct()
        return list((await self.s.execute(stmt)).scalars())

    async def list_ids(self, tenant_id: TenantId) -> list[uuid.UUID]:
        stmt = (
            select(Account.id).where(Account.tenant_id == tenant_id).order_by(Account.external_ref)
        )
        return list((await self.s.execute(stmt)).scalars())

    async def upsert(self, tenant_id: TenantId, fields: dict[str, Any]) -> Account:
        """Insert or update by (tenant, external_ref); safe under concurrent ingests."""
        stmt = insert(Account).values(id=uuid.uuid4(), tenant_id=tenant_id, **fields)
        upsert = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "external_ref"],
            set_={k: stmt.excluded[k] for k in fields if k != "external_ref"},
        ).returning(Account)
        result = await self.s.execute(upsert.execution_options(populate_existing=True))
        account: Account = result.scalar_one()
        return account

    async def set_opted_out(self, tenant_id: TenantId, phone_e164: str, at: datetime) -> int:
        result = await self.s.execute(
            update(Account)
            .where(Account.tenant_id == tenant_id, Account.phone_e164 == phone_e164)
            .values(opted_out_at=at)
        )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def set_contact_flag(self, tenant_id: TenantId, account_id: uuid.UUID, flag: str) -> None:
        await self.s.execute(
            update(Account)
            .where(Account.tenant_id == tenant_id, Account.id == account_id)
            .values(contact_flag=flag)
        )


class InvoicesRepo(Repo):
    async def for_account(self, tenant_id: TenantId, account_id: uuid.UUID) -> list[Invoice]:
        stmt = (
            select(Invoice)
            .where(Invoice.tenant_id == tenant_id, Invoice.account_id == account_id)
            .order_by(Invoice.due_on)
        )
        return list((await self.s.execute(stmt)).scalars())

    async def get_by_number(self, tenant_id: TenantId, number: str) -> Invoice | None:
        stmt = select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.number == number)
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def upsert(
        self, tenant_id: TenantId, account_id: uuid.UUID, fields: dict[str, Any]
    ) -> Invoice:
        """Insert or update by (tenant, number). A disputed invoice is never reopened by an
        aging re-import that says "open": disputes are resolved explicitly, not by ingest."""
        stmt = insert(Invoice).values(
            id=uuid.uuid4(), tenant_id=tenant_id, account_id=account_id, **fields
        )
        keep_dispute = (Invoice.status == "disputed") & (stmt.excluded.status == "open")
        updates: dict[str, Any] = {k: stmt.excluded[k] for k in fields if k != "number"}
        updates["account_id"] = account_id
        updates["status"] = case((keep_dispute, Invoice.status), else_=stmt.excluded.status)
        updates["dispute_reason"] = case(
            (keep_dispute, Invoice.dispute_reason), else_=stmt.excluded.dispute_reason
        )
        upsert = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "number"], set_=updates
        ).returning(Invoice)
        result = await self.s.execute(upsert.execution_options(populate_existing=True))
        invoice: Invoice = result.scalar_one()
        return invoice

    async def mark_disputed(
        self,
        tenant_id: TenantId,
        account_id: uuid.UUID,
        reason: str,
        numbers: list[str] | None = None,
    ) -> int:
        stmt = update(Invoice).where(
            Invoice.tenant_id == tenant_id,
            Invoice.account_id == account_id,
            Invoice.status == "open",
        )
        if numbers:
            stmt = stmt.where(Invoice.number.in_(numbers))
        result = await self.s.execute(stmt.values(status="disputed", dispute_reason=reason))
        return int(result.rowcount or 0)  # type: ignore[attr-defined]


class NotesRepo(Repo):
    async def for_account(
        self, tenant_id: TenantId, account_id: uuid.UUID, limit: int = 50
    ) -> list[Note]:
        stmt = (
            select(Note)
            .where(Note.tenant_id == tenant_id, Note.account_id == account_id)
            .order_by(Note.created_at.desc())
            .limit(limit)
        )
        return list((await self.s.execute(stmt)).scalars())

    async def add(self, tenant_id: TenantId, account_id: uuid.UUID, source: str, body: str) -> Note:
        note = Note(tenant_id=tenant_id, account_id=account_id, source=source, body=body)
        self.s.add(note)
        await self.s.flush()
        return note


class QueueRepo(Repo):
    """Prioritized queue: accounts by cached score, keyset-paginated on (score DESC, id)."""

    async def refresh_scores(
        self, tenant_id: TenantId, today: date, account_ids: list[uuid.UUID] | None = None
    ) -> int:
        from collections_copilot.scoring.priority import inputs_from, score

        stmt = select(Account).where(Account.tenant_id == tenant_id)
        if account_ids is not None:
            stmt = stmt.where(Account.id.in_(account_ids))
        accounts = list((await self.s.execute(stmt)).scalars())
        if not accounts:
            return 0
        ids = [a.id for a in accounts]
        invoices: dict[uuid.UUID, list[Invoice]] = {i: [] for i in ids}
        for inv in (
            await self.s.execute(
                select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.account_id.in_(ids))
            )
        ).scalars():
            invoices[inv.account_id].append(inv)
        since = today - timedelta(days=90)
        broken_rows = await self.s.execute(
            select(Promise.account_id, func.count())
            .where(
                Promise.tenant_id == tenant_id,
                Promise.account_id.in_(ids),
                Promise.status == "broken",
                Promise.promised_date >= since,
            )
            .group_by(Promise.account_id)
        )
        broken = {row[0]: int(row[1]) for row in broken_rows}
        now = datetime.now(UTC)
        for acc in accounts:
            rows = [
                (i.amount_cents, i.status, max(0, (today - i.due_on).days))
                for i in invoices[acc.id]
            ]
            inputs = inputs_from(
                invoices=rows,
                broken_promises_90d=broken.get(acc.id, 0),
                segment=acc.segment,
                currency=acc.currency,
            )
            result = score(inputs)
            acc.priority_score = result.score
            acc.score_components = result.components
            acc.open_balance_cents = inputs.balance_cents
            acc.max_days_past_due = inputs.max_days_past_due
            acc.score_refreshed_at = now
        await self.s.flush()
        return len(accounts)

    async def page(
        self,
        tenant_id: TenantId,
        *,
        limit: int,
        after: tuple[int, uuid.UUID] | None = None,
        segment: str | None = None,
        min_score: int | None = None,
        status: str | None = None,
        search: str | None = None,
    ) -> list[tuple[Account, Case | None]]:
        open_case = and_(
            Case.account_id == Account.id,
            Case.tenant_id == tenant_id,
            Case.status.in_(OPEN_STATUSES),
        )
        stmt = (
            select(Account, Case).outerjoin(Case, open_case).where(Account.tenant_id == tenant_id)
        )
        if after is not None:
            score_after, id_after = after
            stmt = stmt.where(
                or_(
                    Account.priority_score < score_after,
                    and_(Account.priority_score == score_after, Account.id > id_after),
                )
            )
        if segment:
            stmt = stmt.where(Account.segment == segment)
        if min_score is not None:
            stmt = stmt.where(Account.priority_score >= min_score)
        if status == "no_case":
            stmt = stmt.where(Case.id.is_(None))
        elif status:
            stmt = stmt.where(Case.status == status)
        if search:
            like = f"%{search.replace('%', '').replace('_', '')}%"
            stmt = stmt.where(or_(Account.name.ilike(like), Account.external_ref.ilike(like)))
        stmt = stmt.order_by(Account.priority_score.desc(), Account.id).limit(limit)
        return [(row[0], row[1]) for row in (await self.s.execute(stmt)).all()]

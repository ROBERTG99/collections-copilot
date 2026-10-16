from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select, update

from collections_copilot.db.models import Account, Invoice, Note
from collections_copilot.db.repositories.base import Repo, TenantId, as_uuid


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

    async def list_ids(self, tenant_id: TenantId) -> list[uuid.UUID]:
        stmt = (
            select(Account.id).where(Account.tenant_id == tenant_id).order_by(Account.external_ref)
        )
        return list((await self.s.execute(stmt)).scalars())

    async def upsert(self, tenant_id: TenantId, fields: dict[str, Any]) -> Account:
        existing = await self.get_by_ref(tenant_id, fields["external_ref"])
        if existing is None:
            account = Account(tenant_id=tenant_id, **fields)
            self.s.add(account)
            await self.s.flush()
            return account
        for key, value in fields.items():
            setattr(existing, key, value)
        await self.s.flush()
        return existing

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
        existing = await self.get_by_number(tenant_id, fields["number"])
        if existing is None:
            invoice = Invoice(tenant_id=tenant_id, account_id=account_id, **fields)
            self.s.add(invoice)
            await self.s.flush()
            return invoice
        for key, value in fields.items():
            setattr(existing, key, value)
        existing.account_id = account_id
        await self.s.flush()
        return existing

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

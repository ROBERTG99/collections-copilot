"""Repositories for promises, messages, contacts, opt-outs, follow-ups and step records."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from collections_copilot.db.models import (
    ContactLog,
    FollowUp,
    Message,
    MessageStatusEvent,
    OptOut,
    Promise,
    StepRecordRow,
)
from collections_copilot.db.repositories.base import Repo, TenantId, as_uuid


class PromisesRepo(Repo):
    async def broken_since(self, tenant_id: TenantId, account_id: uuid.UUID, since: date) -> int:
        stmt = select(func.count()).where(
            Promise.tenant_id == tenant_id,
            Promise.account_id == account_id,
            Promise.status == "broken",
            Promise.promised_date >= since,
        )
        return int((await self.s.execute(stmt)).scalar_one())

    async def by_key(self, tenant_id: TenantId, key: str) -> Promise | None:
        stmt = select(Promise).where(Promise.tenant_id == tenant_id, Promise.idempotency_key == key)
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def get(self, tenant_id: TenantId, promise_id: uuid.UUID | str) -> Promise | None:
        pid = as_uuid(promise_id)
        if pid is None:
            return None
        stmt = select(Promise).where(Promise.tenant_id == tenant_id, Promise.id == pid)
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def get_for_update(self, tenant_id: TenantId, promise_id: uuid.UUID) -> Promise | None:
        stmt = (
            select(Promise)
            .where(Promise.tenant_id == tenant_id, Promise.id == promise_id)
            .with_for_update()
        )
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def by_approval_id(self, approval_id: str) -> Promise | None:
        """Gateway webhooks identify a held write by approval id only (tenant checked by caller)."""
        stmt = select(Promise).where(Promise.mcp_approval_id == approval_id)
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def create_if_absent(self, tenant_id: TenantId, **fields: Any) -> Promise:
        stmt = (
            insert(Promise)
            .values(id=uuid.uuid4(), tenant_id=tenant_id, **fields)
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
        )
        await self.s.execute(stmt)
        promise = await self.by_key(tenant_id, fields["idempotency_key"])
        assert promise is not None
        return promise

    async def set(self, tenant_id: TenantId, promise_id: uuid.UUID, **fields: Any) -> None:
        await self.s.execute(
            update(Promise)
            .where(Promise.tenant_id == tenant_id, Promise.id == promise_id)
            .values(**fields)
        )

    async def for_account(
        self, tenant_id: TenantId, account_id: uuid.UUID, limit: int | None = None
    ) -> list[Promise]:
        stmt = (
            select(Promise)
            .where(Promise.tenant_id == tenant_id, Promise.account_id == account_id)
            .order_by(Promise.created_at.desc(), Promise.id)
            .limit(limit)
        )
        return list((await self.s.execute(stmt)).scalars())

    async def list(
        self, tenant_id: TenantId, status: str | None = None, limit: int = 100
    ) -> list[Promise]:
        stmt = select(Promise).where(Promise.tenant_id == tenant_id)
        if status:
            stmt = stmt.where(Promise.status == status)
        stmt = stmt.order_by(Promise.created_at.desc()).limit(limit)
        return list((await self.s.execute(stmt)).scalars())


class MessagesRepo(Repo):
    async def by_key(self, tenant_id: TenantId, key: str) -> Message | None:
        stmt = select(Message).where(Message.tenant_id == tenant_id, Message.idempotency_key == key)
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def claim(self, tenant_id: TenantId, key: str, **fields: Any) -> tuple[Message, bool]:
        """Insert the outbound row before calling the provider. Returns (row, created)."""
        stmt = (
            insert(Message)
            .values(id=uuid.uuid4(), tenant_id=tenant_id, idempotency_key=key, **fields)
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(Message.id)
        )
        created = (await self.s.execute(stmt)).scalar_one_or_none() is not None
        row = await self.by_key(tenant_id, key)
        assert row is not None
        return row, created

    async def by_sid(self, provider_sid: str) -> Message | None:
        """Provider callbacks identify messages by SID only (resolves the tenant)."""
        stmt = select(Message).where(Message.provider_sid == provider_sid)
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def get(self, tenant_id: TenantId, message_id: uuid.UUID | str) -> Message | None:
        mid = as_uuid(message_id)
        if mid is None:
            return None
        stmt = select(Message).where(Message.tenant_id == tenant_id, Message.id == mid)
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def latest_outbound_to(self, phone_e164: str) -> Message | None:
        """Inbound SMS carry only the sender's number; the latest outbound message to it
        resolves the tenant, account and case."""
        stmt = (
            select(Message)
            .where(Message.direction == "outbound", Message.to_e164 == phone_e164)
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def latest_outbound_per_tenant(self, phone_e164: str, since: datetime) -> list[Message]:
        """Webhook routing (cross-tenant by design): each tenant's latest outbound message to a
        number since `since`. More than one row means the reply cannot be attributed by the
        sender alone."""
        latest = (
            select(Message.tenant_id, func.max(Message.created_at).label("at"))
            .where(
                Message.direction == "outbound",
                Message.to_e164 == phone_e164,
                Message.created_at >= since,
            )
            .group_by(Message.tenant_id)
            .subquery()
        )
        stmt = (
            select(Message)
            .join(
                latest,
                (Message.tenant_id == latest.c.tenant_id) & (Message.created_at == latest.c.at),
            )
            .where(Message.direction == "outbound", Message.to_e164 == phone_e164)
        )
        rows: dict[uuid.UUID, Message] = {}
        for row in (await self.s.execute(stmt)).scalars():
            rows.setdefault(row.tenant_id, row)
        return list(rows.values())

    async def stale_received(self, before: datetime, limit: int = 50) -> list[Message]:
        """Scheduler sweep (cross-tenant by design): inbound replies never handed to the reply
        graph (the enqueue failed after the row was stored)."""
        stmt = (
            select(Message)
            .where(
                Message.direction == "inbound",
                Message.status == "received",
                Message.created_at < before,
            )
            .order_by(Message.created_at)
            .limit(limit)
        )
        return list((await self.s.execute(stmt)).scalars())

    async def add_inbound(self, tenant_id: TenantId, **fields: Any) -> tuple[Message, bool]:
        stmt = (
            insert(Message)
            .values(id=uuid.uuid4(), tenant_id=tenant_id, direction="inbound", **fields)
            .on_conflict_do_nothing(index_elements=["provider_sid"])
            .returning(Message.id)
        )
        created = (await self.s.execute(stmt)).scalar_one_or_none() is not None
        row = (
            await self.s.execute(
                select(Message).where(Message.provider_sid == fields["provider_sid"])
            )
        ).scalar_one()
        return row, created

    async def mark_handled(self, tenant_id: TenantId, message_id: uuid.UUID) -> bool:
        """Claim an inbound reply's side effects. False when another run already applied them."""
        result = await self.s.execute(
            update(Message)
            .where(
                Message.tenant_id == tenant_id,
                Message.id == message_id,
                Message.status != "handled",
            )
            .values(status="handled")
            .returning(Message.id)
        )
        return result.scalar_one_or_none() is not None

    async def set(self, tenant_id: TenantId, message_id: uuid.UUID, **fields: Any) -> None:
        await self.s.execute(
            update(Message)
            .where(Message.tenant_id == tenant_id, Message.id == message_id)
            .values(**fields)
        )

    async def for_account(
        self, tenant_id: TenantId, account_id: uuid.UUID, limit: int = 50
    ) -> list[Message]:
        stmt = (
            select(Message)
            .where(Message.tenant_id == tenant_id, Message.account_id == account_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        return list((await self.s.execute(stmt)).scalars())

    async def inbound(self, tenant_id: TenantId, limit: int = 100) -> list[Message]:
        stmt = (
            select(Message)
            .where(Message.tenant_id == tenant_id, Message.direction == "inbound")
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        return list((await self.s.execute(stmt)).scalars())

    async def add_status_event(
        self, tenant_id: TenantId, sid: str, status: str, payload: dict[str, Any]
    ) -> bool:
        stmt = (
            insert(MessageStatusEvent)
            .values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                provider_sid=sid,
                status=status,
                payload=payload,
            )
            .on_conflict_do_nothing(index_elements=["provider_sid", "status"])
            .returning(MessageStatusEvent.id)
        )
        return (await self.s.execute(stmt)).scalar_one_or_none() is not None


class ContactsRepo(Repo):
    async def recent(
        self, tenant_id: TenantId, account_id: uuid.UUID, now: datetime, days: int = 7
    ) -> list[datetime]:
        stmt = select(ContactLog.sent_at).where(
            ContactLog.tenant_id == tenant_id,
            ContactLog.account_id == account_id,
            ContactLog.sent_at > now - timedelta(days=days),
        )
        return list((await self.s.execute(stmt)).scalars())

    async def add(
        self, tenant_id: TenantId, account_id: uuid.UUID, message_id: uuid.UUID, sent_at: datetime
    ) -> None:
        self.s.add(
            ContactLog(
                tenant_id=tenant_id, account_id=account_id, message_id=message_id, sent_at=sent_at
            )
        )
        await self.s.flush()

    async def add_once(
        self, tenant_id: TenantId, account_id: uuid.UUID, message_id: uuid.UUID, sent_at: datetime
    ) -> bool:
        """One contact-log row per message, so a re-run after a crash never double-counts."""
        exists = await self.s.execute(
            select(ContactLog.id).where(
                ContactLog.tenant_id == tenant_id, ContactLog.message_id == message_id
            )
        )
        if exists.first() is not None:
            return False
        await self.add(tenant_id, account_id, message_id, sent_at)
        return True


class OptOutsRepo(Repo):
    async def is_opted_out(self, tenant_id: TenantId, phone_e164: str) -> bool:
        stmt = select(OptOut.phone_e164).where(
            OptOut.tenant_id == tenant_id, OptOut.phone_e164 == phone_e164
        )
        return (await self.s.execute(stmt)).scalar_one_or_none() is not None

    async def add(
        self, tenant_id: TenantId, phone_e164: str, source: str, created_by: str | None = None
    ) -> bool:
        stmt = (
            insert(OptOut)
            .values(
                tenant_id=tenant_id, phone_e164=phone_e164, source=source, created_by=created_by
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "phone_e164"])
            .returning(OptOut.phone_e164)
        )
        return (await self.s.execute(stmt)).scalar_one_or_none() is not None

    async def get(self, tenant_id: TenantId, phone_e164: str) -> OptOut | None:
        stmt = select(OptOut).where(OptOut.tenant_id == tenant_id, OptOut.phone_e164 == phone_e164)
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def list(self, tenant_id: TenantId, limit: int = 200) -> list[OptOut]:
        stmt = (
            select(OptOut)
            .where(OptOut.tenant_id == tenant_id)
            .order_by(OptOut.created_at.desc())
            .limit(limit)
        )
        return list((await self.s.execute(stmt)).scalars())


class FollowUpsRepo(Repo):
    async def add(self, tenant_id: TenantId, **fields: Any) -> FollowUp:
        row = FollowUp(tenant_id=tenant_id, **fields)
        self.s.add(row)
        await self.s.flush()
        return row

    async def pending_for_case(
        self,
        tenant_id: TenantId,
        case_id: uuid.UUID,
        kind: str,
        due_after: datetime | None = None,
    ) -> FollowUp | None:
        """A pending follow-up of this kind; with `due_after`, only one still in the future (a
        past-due row may be the one the scheduler is processing right now)."""
        stmt = select(FollowUp).where(
            FollowUp.tenant_id == tenant_id,
            FollowUp.case_id == case_id,
            FollowUp.kind == kind,
            FollowUp.status == "pending",
        )
        if due_after is not None:
            stmt = stmt.where(FollowUp.due_at > due_after)
        return (await self.s.execute(stmt)).scalars().first()

    async def cancel_for_case(self, tenant_id: TenantId, case_id: uuid.UUID) -> None:
        await self.s.execute(
            update(FollowUp)
            .where(
                FollowUp.tenant_id == tenant_id,
                FollowUp.case_id == case_id,
                FollowUp.status == "pending",
            )
            .values(status="cancelled")
        )

    async def claim_due(self, now: datetime, limit: int = 50) -> list[FollowUp]:
        """Scheduler: claim due rows across tenants with FOR UPDATE SKIP LOCKED."""
        stmt = (
            select(FollowUp)
            .where(FollowUp.status == "pending", FollowUp.due_at <= now)
            .order_by(FollowUp.due_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = list((await self.s.execute(stmt)).scalars())
        for row in rows:
            row.status = "claimed"
        await self.s.flush()
        return rows

    async def set(self, tenant_id: TenantId, follow_up_id: uuid.UUID, **fields: Any) -> None:
        await self.s.execute(
            update(FollowUp)
            .where(FollowUp.tenant_id == tenant_id, FollowUp.id == follow_up_id)
            .values(**fields)
        )

    async def list(
        self, tenant_id: TenantId, status: str | None = None, limit: int = 100
    ) -> list[FollowUp]:
        stmt = select(FollowUp).where(FollowUp.tenant_id == tenant_id)
        if status:
            stmt = stmt.where(FollowUp.status == status)
        stmt = stmt.order_by(FollowUp.due_at).limit(limit)
        return list((await self.s.execute(stmt)).scalars())


class StepsRepo(Repo):
    async def add(self, tenant_id: TenantId, **fields: Any) -> None:
        self.s.add(StepRecordRow(tenant_id=tenant_id, **fields))
        await self.s.flush()

    async def for_case(self, tenant_id: TenantId, case_id: uuid.UUID) -> list[StepRecordRow]:
        stmt = (
            select(StepRecordRow)
            .where(StepRecordRow.tenant_id == tenant_id, StepRecordRow.case_id == case_id)
            .order_by(StepRecordRow.id)
        )
        return list((await self.s.execute(stmt)).scalars())

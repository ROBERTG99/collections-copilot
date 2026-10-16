"""Synthetic test data builders (phone numbers only in the +1555 / +34555 ranges)."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collections_copilot.db.models import Account, Invoice, Note, Tenant


async def make_tenant(sm: async_sessionmaker[AsyncSession], name: str | None = None) -> uuid.UUID:
    tid = uuid.uuid4()
    async with sm() as s, s.begin():
        s.add(Tenant(id=tid, name=name or f"tenant-{tid.hex[:8]}"))
    return tid


async def make_account(
    sm: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    *,
    invoices: list[dict[str, Any]] | None = None,
    notes: list[str] | None = None,
    **fields: Any,
) -> uuid.UUID:
    aid = uuid.uuid4()
    n = aid.int % 10_000
    defaults: dict[str, Any] = {
        "external_ref": f"ACC-{aid.hex[:8]}",
        "name": f"Synthetic Co {n}",
        "phone_e164": f"+1555{n:07d}",
        "timezone": "America/New_York",
        "segment": "smb",
        "language": "en",
        "currency": "USD",
    }
    defaults.update(fields)
    async with sm() as s, s.begin():
        s.add(Account(id=aid, tenant_id=tenant_id, **defaults))
        await s.flush()
        for i, inv in enumerate(invoices or [{"amount_cents": 120_000, "days_past_due": 30}]):
            dpd = inv.get("days_past_due", 30)
            s.add(
                Invoice(
                    tenant_id=tenant_id,
                    account_id=aid,
                    number=inv.get("number", f"INV-{aid.hex[:6]}-{i}"),
                    amount_cents=inv["amount_cents"],
                    currency=defaults["currency"],
                    issued_on=date.today() - timedelta(days=dpd + 30),
                    due_on=date.today() - timedelta(days=dpd),
                    status=inv.get("status", "open"),
                    dispute_reason=inv.get("dispute_reason"),
                )
            )
        for body in notes or []:
            s.add(Note(tenant_id=tenant_id, account_id=aid, source="crm", body=body))
    return aid

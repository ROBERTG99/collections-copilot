"""T-11: loading gen.v1 is complete and idempotent by digest."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from collections_copilot.db.models import Account, Invoice
from data.generator.gen import generate_tenant
from data.generator.load import load_tenant

pytestmark = pytest.mark.integration


async def test_load_is_idempotent(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    spec = generate_tenant("seed-probe-tenant", 25, seed=99, prefix="P")
    assert await load_tenant(sessionmaker, spec) is True
    assert await load_tenant(sessionmaker, spec) is False
    async with sessionmaker() as s:
        n_acc = await s.scalar(
            select(func.count()).select_from(Account).where(Account.tenant_id == spec.tenant_id)
        )
        n_inv = await s.scalar(
            select(func.count()).select_from(Invoice).where(Invoice.tenant_id == spec.tenant_id)
        )
    assert n_acc == 25
    assert n_inv == sum(len(a.invoices) for a in spec.accounts)

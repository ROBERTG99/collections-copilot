"""Repositories are tenant-scoped; a tenant-B id read with tenant A returns None."""

from __future__ import annotations

import pytest

from collections_copilot.db.repositories import Repos
from tests.factories import make_account, make_tenant

pytestmark = pytest.mark.integration


async def test_cross_tenant_read_returns_none(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    tenant_a = await make_tenant(sessionmaker)
    tenant_b = await make_tenant(sessionmaker)
    account_b = await make_account(sessionmaker, tenant_b, notes=["call back next week"])
    async with sessionmaker() as session:
        repos = Repos(session)
        assert await repos.accounts.get(tenant_a, account_b) is None
        assert await repos.accounts.get(tenant_b, account_b) is not None
        assert await repos.invoices.for_account(tenant_a, account_b) == []
        assert len(await repos.invoices.for_account(tenant_b, account_b)) == 1
        assert await repos.notes.for_account(tenant_a, account_b) == []
        assert len(await repos.notes.for_account(tenant_b, account_b)) == 1
        case_b = await repos.cases.create(tenant_b, account_b, status="queued")
        await session.commit()
        assert await repos.cases.get(tenant_a, case_b.id) is None
        assert (await repos.cases.get(tenant_b, case_b.id)) is not None


def test_raw_sql_is_banned_outside_db() -> None:
    import tomllib
    from pathlib import Path

    cfg = tomllib.loads((Path(__file__).resolve().parents[3] / "pyproject.toml").read_text())
    banned = cfg["tool"]["ruff"]["lint"]["flake8-tidy-imports"]["banned-api"]
    assert "sqlalchemy.text" in banned

"""T-10: `cc audit verify` exits 0 on an intact chain and 1 with the first bad seq after tampering."""

from __future__ import annotations

import asyncio

import psycopg
import pytest
from typer.testing import CliRunner

from collections_copilot.cli import app
from collections_copilot.db import audit
from tests.factories import make_tenant

pytestmark = pytest.mark.integration


async def test_cli_detects_tamper(sessionmaker, database_url: str, migrated_database: str) -> None:  # type: ignore[no-untyped-def]
    tenant = await make_tenant(sessionmaker)
    seqs = []
    for i in range(5):
        async with sessionmaker() as s, s.begin():
            row = await audit.append(
                s,
                tenant,
                actor_type="user",
                actor_id="ana.analyst",
                action="approval.approve",
                entity="case",
                entity_id=str(i),
                payload={"to_e164": "+15550001111", "n": i},
            )
            seqs.append(row.seq)
    async with sessionmaker() as s:
        assert row.payload_redacted["to_e164"] == "[redacted]"
        assert await audit.verify_tenant(s, tenant) is None

    runner = CliRunner()
    ok = await asyncio.to_thread(
        runner.invoke,
        app,
        ["audit", "verify", "--tenant", str(tenant), "--database-url", database_url],
    )
    assert ok.exit_code == 0, ok.output
    assert "chain_ok=true" in ok.output

    with psycopg.connect(migrated_database, autocommit=True) as conn:
        conn.execute("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update")
        try:
            conn.execute(
                "UPDATE audit_events SET action='approval.reject' WHERE seq=%s", (seqs[2],)
            )
        finally:
            conn.execute("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
    bad = await asyncio.to_thread(
        runner.invoke,
        app,
        ["audit", "verify", "--tenant", str(tenant), "--database-url", database_url],
    )
    assert bad.exit_code == 1
    assert f"first_bad_seq={seqs[2]}" in bad.output

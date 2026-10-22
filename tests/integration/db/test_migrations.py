"""Migration 0001 upgrades, downgrades cleanly and enforces the schema constraints."""

from __future__ import annotations

import uuid

import psycopg
import pytest
from alembic import command

from tests.conftest import alembic_config

pytestmark = pytest.mark.integration

TABLES = {
    "tenants",
    "accounts",
    "invoices",
    "notes",
    "ingest_batches",
    "cases",
    "drafts",
    "approvals",
    "messages",
    "message_status_events",
    "promises",
    "follow_ups",
    "opt_outs",
    "contact_log",
    "settings",
    "step_records",
    "audit_events",
    "api_idempotency",
}


def _tables(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
    ).fetchall()
    return {r[0] for r in rows} - {"alembic_version"}


def test_upgrade_downgrade_and_constraints(empty_database: str) -> None:
    cfg = alembic_config(empty_database)
    command.upgrade(cfg, "head")
    with psycopg.connect(empty_database, autocommit=True) as conn:
        assert _tables(conn) == TABLES
        for table in TABLES - {"tenants"}:
            nullable = conn.execute(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name=%s AND column_name='tenant_id'",
                (table,),
            ).fetchone()
            assert nullable == ("NO",), table

        tenant = uuid.uuid4()
        conn.execute("INSERT INTO tenants (id, name) VALUES (%s, 'acme')", (tenant,))
        account = uuid.uuid4()
        conn.execute(
            "INSERT INTO accounts (id, tenant_id, external_ref, name, phone_e164, timezone, segment,"
            " language) VALUES (%s,%s,'A-1','Acme','+15550000001','America/New_York','smb','en')",
            (account, tenant),
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO accounts (id, tenant_id, external_ref, name, phone_e164, timezone,"
                " segment, language) VALUES (%s,%s,'A-2','X','+15550000002','UTC','smb','fr')",
                (uuid.uuid4(), tenant),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO invoices (id, tenant_id, account_id, number, amount_cents, currency,"
                " issued_on, due_on, status) VALUES (%s,%s,%s,'INV-1',-5,'USD',current_date,"
                " current_date,'open')",
                (uuid.uuid4(), tenant, account),
            )
        # one open case per account
        conn.execute(
            "INSERT INTO cases (id, tenant_id, account_id, status) VALUES (%s,%s,%s,'queued')",
            (uuid.uuid4(), tenant, account),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO cases (id, tenant_id, account_id, status) VALUES (%s,%s,%s,'queued')",
                (uuid.uuid4(), tenant, account),
            )
        conn.execute(
            "INSERT INTO cases (id, tenant_id, account_id, status) VALUES (%s,%s,%s,'closed')",
            (uuid.uuid4(), tenant, account),
        )
        # audit is append-only
        conn.execute(
            "INSERT INTO audit_events (tenant_id, actor_type, actor_id, action, entity,"
            " payload_redacted, prev_hash, hash) VALUES (%s,'system','t','x','y','{}',%s,%s)",
            (tenant, "0" * 64, "a" * 64),
        )
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("UPDATE audit_events SET action='z'")
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("DELETE FROM audit_events")

    command.downgrade(cfg, "base")
    with psycopg.connect(empty_database, autocommit=True) as conn:
        assert _tables(conn) == set()
    command.upgrade(cfg, "head")

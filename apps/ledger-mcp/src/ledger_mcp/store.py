"""Postgres store for the synthetic ledger (schema `ledger`)."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

DDL = """
CREATE SCHEMA IF NOT EXISTS ledger;
CREATE TABLE IF NOT EXISTS ledger.accounts (
  tenant text NOT NULL, account_ref text NOT NULL, name text NOT NULL, currency char(3) NOT NULL,
  PRIMARY KEY (tenant, account_ref)
);
CREATE TABLE IF NOT EXISTS ledger.invoices (
  tenant text NOT NULL, number text NOT NULL, account_ref text NOT NULL,
  amount_cents bigint NOT NULL CHECK (amount_cents > 0), currency char(3) NOT NULL,
  due_on date NOT NULL, status text NOT NULL DEFAULT 'open',
  PRIMARY KEY (tenant, number)
);
CREATE TABLE IF NOT EXISTS ledger.promises (
  id uuid PRIMARY KEY, tenant text NOT NULL, account_ref text NOT NULL,
  amount_cents bigint NOT NULL CHECK (amount_cents > 0), currency char(3) NOT NULL,
  promised_date date NOT NULL, invoice_numbers text[] NOT NULL, source_message_sid text NOT NULL,
  idempotency_key text NOT NULL, ledger_ref text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant, idempotency_key)
);
ALTER TABLE ledger.promises ADD COLUMN IF NOT EXISTS args_sha256 text;
CREATE TABLE IF NOT EXISTS ledger.notes (
  id uuid PRIMARY KEY, tenant text NOT NULL, account_ref text NOT NULL, body text NOT NULL,
  idempotency_key text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant, idempotency_key)
);
"""


class IdempotencyConflictError(Exception):
    """Same idempotency key, different arguments."""


class LedgerStore:
    def __init__(self, dsn: str) -> None:
        self.pool = AsyncConnectionPool(dsn, min_size=1, max_size=10, open=False)

    async def open(self) -> None:
        await self.pool.open()
        async with self.pool.connection() as conn:
            await conn.execute("SELECT pg_advisory_lock(424203)")
            try:
                await conn.execute(DDL)
            finally:
                await conn.execute("SELECT pg_advisory_unlock(424203)")

    async def close(self) -> None:
        await self.pool.close()

    async def upsert_account(self, tenant: str, ref: str, name: str, currency: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO ledger.accounts VALUES (%s,%s,%s,%s) ON CONFLICT (tenant, account_ref) "
                "DO UPDATE SET name=EXCLUDED.name, currency=EXCLUDED.currency",
                (tenant, ref, name, currency),
            )

    async def upsert_invoice(
        self,
        tenant: str,
        ref: str,
        number: str,
        amount_cents: int,
        currency: str,
        due_on: date,
        status: str = "open",
    ) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO ledger.invoices VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (tenant, number) "
                "DO UPDATE SET amount_cents=EXCLUDED.amount_cents, due_on=EXCLUDED.due_on, status=EXCLUDED.status",
                (tenant, number, ref, amount_cents, currency, due_on, status),
            )

    async def account(self, tenant: str, ref: str) -> dict[str, Any] | None:
        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM ledger.accounts WHERE tenant=%s AND account_ref=%s", (tenant, ref)
            )
            return await cur.fetchone()

    async def open_invoices(self, tenant: str, ref: str) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT number, amount_cents, currency, due_on FROM ledger.invoices "
                "WHERE tenant=%s AND account_ref=%s AND status='open' ORDER BY due_on, number",
                (tenant, ref),
            )
            return list(await cur.fetchall())

    async def record_promise(
        self, tenant: str, key: str, fields: dict[str, Any]
    ) -> tuple[str, bool]:
        """Insert once per (tenant, key); returns (ledger_ref, replayed). A replay with different
        arguments raises IdempotencyConflictError."""
        ledger_ref = f"LP-{uuid.uuid4().hex[:12].upper()}"
        args_sha = hashlib.sha256(
            json.dumps(fields, sort_keys=True, default=str).encode()
        ).hexdigest()
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO ledger.promises (id, tenant, account_ref, amount_cents, currency, promised_date,"
                " invoice_numbers, source_message_sid, idempotency_key, ledger_ref, args_sha256) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (tenant, idempotency_key) DO NOTHING "
                "RETURNING ledger_ref",
                (
                    uuid.uuid4(),
                    tenant,
                    fields["account_ref"],
                    fields["amount_cents"],
                    fields["currency"],
                    fields["promised_date"],
                    fields["invoice_numbers"],
                    fields["source_message_sid"],
                    key,
                    ledger_ref,
                    args_sha,
                ),
            )
            row = await cur.fetchone()
            if row is not None:
                return str(row[0]), False
            cur = await conn.execute(
                "SELECT ledger_ref, args_sha256 FROM ledger.promises "
                "WHERE tenant=%s AND idempotency_key=%s",
                (tenant, key),
            )
            existing = await cur.fetchone()
            assert existing is not None
            if existing[1] is not None and existing[1] != args_sha:
                raise IdempotencyConflictError(key)
            return str(existing[0]), True

    async def invoices_belong(self, tenant: str, ref: str, numbers: list[str]) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT count(DISTINCT number) FROM ledger.invoices "
                "WHERE tenant=%s AND account_ref=%s AND number = ANY(%s)",
                (tenant, ref, numbers),
            )
            row = await cur.fetchone()
            return row is not None and int(row[0]) == len(set(numbers))

    async def add_note(self, tenant: str, key: str, ref: str, body: str) -> tuple[str, bool]:
        note_id = uuid.uuid4()
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO ledger.notes (id, tenant, account_ref, body, idempotency_key) VALUES "
                "(%s,%s,%s,%s,%s) ON CONFLICT (tenant, idempotency_key) DO NOTHING RETURNING id",
                (note_id, tenant, ref, body, key),
            )
            row = await cur.fetchone()
            if row is not None:
                return str(row[0]), False
            cur = await conn.execute(
                "SELECT id FROM ledger.notes WHERE tenant=%s AND idempotency_key=%s", (tenant, key)
            )
            existing = await cur.fetchone()
            assert existing is not None
            return str(existing[0]), True

    async def promise_count(self, tenant: str) -> int:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT count(*) FROM ledger.promises WHERE tenant=%s", (tenant,)
            )
            row = await cur.fetchone()
            return int(row[0]) if row else 0

    async def promises_for(self, tenant: str, ref: str) -> list[dict[str, Any]]:
        """The account's promise rows (the demo counts them and checks their keys)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT ledger_ref, idempotency_key, source_message_sid FROM ledger.promises "
                "WHERE tenant=%s AND account_ref=%s ORDER BY ledger_ref",
                (tenant, ref),
            )
            cols = [c.name for c in cur.description or []]
            return [dict(zip(cols, r, strict=True)) for r in await cur.fetchall()]

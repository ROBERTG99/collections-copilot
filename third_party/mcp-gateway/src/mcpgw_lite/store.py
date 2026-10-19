"""Postgres persistence for idempotency, approvals and the hash-chained audit (schema mcpgw)."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

DDL = """
CREATE SCHEMA IF NOT EXISTS mcpgw;
CREATE TABLE IF NOT EXISTS mcpgw.idempotency (
  tenant text NOT NULL,
  key text NOT NULL,
  tool text NOT NULL,
  args_sha256 char(64) NOT NULL,
  status text NOT NULL CHECK (status IN ('in_progress', 'completed')),
  result jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant, key)
);
CREATE TABLE IF NOT EXISTS mcpgw.approvals (
  id uuid PRIMARY KEY,
  tenant text NOT NULL,
  agent text NOT NULL,
  tool text NOT NULL,
  idempotency_key text NOT NULL,
  args_sha256 char(64) NOT NULL,
  args_redacted jsonb NOT NULL,
  reasons jsonb NOT NULL,
  status text NOT NULL CHECK (status IN ('pending','approved','rejected','consumed')),
  expires_at timestamptz NOT NULL,
  decided_by text,
  decided_at timestamptz,
  note text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS approvals_key_idx ON mcpgw.approvals (tenant, idempotency_key);
CREATE TABLE IF NOT EXISTS mcpgw.audit_events (
  seq bigserial PRIMARY KEY,
  ts timestamptz NOT NULL DEFAULT now(),
  tenant text NOT NULL,
  agent text NOT NULL,
  server text NOT NULL,
  tool text NOT NULL,
  args_sha256 char(64),
  decision text NOT NULL,
  approval_id uuid,
  idempotency_key text,
  status text NOT NULL,
  latency_ms double precision NOT NULL DEFAULT 0,
  prev_hash char(64) NOT NULL,
  hash char(64) NOT NULL UNIQUE
);
"""

GENESIS = "0" * 64


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def args_sha256(args: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(args).encode()).hexdigest()


class Store:
    def __init__(self, dsn: str) -> None:
        self.pool = AsyncConnectionPool(dsn, min_size=1, max_size=10, open=False)

    async def open(self) -> None:
        await self.pool.open()
        async with self.pool.connection() as conn:
            await conn.execute("SELECT pg_advisory_lock(424201)")
            try:
                await conn.execute(DDL)
            finally:
                await conn.execute("SELECT pg_advisory_unlock(424201)")

    async def close(self) -> None:
        await self.pool.close()

    async def ping(self) -> bool:
        try:
            async with self.pool.connection(timeout=2) as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    # idempotency -------------------------------------------------------------
    async def idem_get(self, tenant: str, key: str) -> dict[str, Any] | None:
        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM mcpgw.idempotency WHERE tenant=%s AND key=%s", (tenant, key)
            )
            return await cur.fetchone()

    async def idem_begin(self, tenant: str, key: str, tool: str, sha: str) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO mcpgw.idempotency (tenant, key, tool, args_sha256, status) "
                "VALUES (%s,%s,%s,%s,'in_progress') ON CONFLICT DO NOTHING",
                (tenant, key, tool, sha),
            )
            return cur.rowcount == 1

    async def idem_complete(self, tenant: str, key: str, result: dict[str, Any]) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE mcpgw.idempotency SET status='completed', result=%s WHERE tenant=%s AND key=%s",
                (Jsonb(result), tenant, key),
            )

    async def idem_abort(self, tenant: str, key: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "DELETE FROM mcpgw.idempotency WHERE tenant=%s AND key=%s AND status='in_progress'",
                (tenant, key),
            )

    # approvals ---------------------------------------------------------------
    async def approval_pending_for_key(self, tenant: str, key: str) -> dict[str, Any] | None:
        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM mcpgw.approvals WHERE tenant=%s AND idempotency_key=%s "
                "AND status='pending' AND expires_at > now() ORDER BY created_at DESC LIMIT 1",
                (tenant, key),
            )
            return await cur.fetchone()

    async def approval_create(
        self,
        tenant: str,
        agent: str,
        tool: str,
        key: str,
        sha: str,
        args_redacted: dict[str, Any],
        reasons: list[str],
        ttl_s: int,
    ) -> dict[str, Any]:
        aid = uuid.uuid4()
        expires = datetime.now(UTC) + timedelta(seconds=ttl_s)
        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "INSERT INTO mcpgw.approvals (id, tenant, agent, tool, idempotency_key, args_sha256,"
                " args_redacted, reasons, status, expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,"
                "'pending',%s) RETURNING *",
                (aid, tenant, agent, tool, key, sha, Jsonb(args_redacted), Jsonb(reasons), expires),
            )
            row = await cur.fetchone()
            assert row is not None
            return row

    async def approval_get(self, approval_id: str) -> dict[str, Any] | None:
        try:
            aid = uuid.UUID(approval_id)
        except ValueError:
            return None
        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM mcpgw.approvals WHERE id=%s", (aid,))
            return await cur.fetchone()

    async def approval_list(self, tenant: str, status: str | None) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            if status:
                await cur.execute(
                    "SELECT * FROM mcpgw.approvals WHERE tenant=%s AND status=%s "
                    "ORDER BY created_at DESC LIMIT 200",
                    (tenant, status),
                )
            else:
                await cur.execute(
                    "SELECT * FROM mcpgw.approvals WHERE tenant=%s ORDER BY created_at DESC LIMIT 200",
                    (tenant,),
                )
            return list(await cur.fetchall())

    async def approval_decide(
        self, approval_id: str, status: str, actor: str, note: str | None
    ) -> dict[str, Any] | None:
        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "UPDATE mcpgw.approvals SET status=%s, decided_by=%s, decided_at=now(), note=%s "
                "WHERE id=%s AND status='pending' AND expires_at > now() RETURNING *",
                (status, actor, note, uuid.UUID(approval_id)),
            )
            return await cur.fetchone()

    async def approval_consume(self, approval_id: str) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE mcpgw.approvals SET status='consumed' WHERE id=%s AND status='approved'",
                (uuid.UUID(approval_id),),
            )
            return cur.rowcount == 1

    # audit -------------------------------------------------------------------
    async def audit(self, event: dict[str, Any]) -> int:
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute("LOCK TABLE mcpgw.audit_events IN EXCLUSIVE MODE")
            cur = await conn.execute(
                "SELECT hash FROM mcpgw.audit_events ORDER BY seq DESC LIMIT 1"
            )
            last = await cur.fetchone()
            prev = last[0] if last else GENESIS
            body = {k: event.get(k) for k in sorted(event)}
            digest = hashlib.sha256((prev + canonical(body)).encode()).hexdigest()
            cur = await conn.execute(
                "INSERT INTO mcpgw.audit_events (tenant, agent, server, tool, args_sha256, "
                "decision, approval_id, idempotency_key, status, latency_ms, prev_hash, hash) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING seq",
                (
                    event["tenant"],
                    event["agent"],
                    event["server"],
                    event["tool"],
                    event.get("args_sha256"),
                    event["decision"],
                    event.get("approval_id"),
                    event.get("idempotency_key"),
                    event["status"],
                    event.get("latency_ms", 0.0),
                    prev,
                    digest,
                ),
            )
            row = await cur.fetchone()
            assert row is not None
            return int(row[0])

"""Append-only, per-tenant SHA-256 hash-chained audit log (FR-12).

hash = sha256(prev_hash + canonical_json(record without seq/prev_hash/hash)); genesis prev_hash is
64 zeros. Appends take a per-tenant transaction advisory lock so the chain stays linear.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from collections_copilot.db.models import AuditEvent

GENESIS = "0" * 64
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_SENSITIVE_KEYS = {
    "phone",
    "phone_e164",
    "to",
    "from",
    "to_e164",
    "from_e164",
    "token",
    "authorization",
}


def redact(value: Any, key: str | None = None) -> Any:
    """Remove phone numbers, emails and secrets from audit payloads."""
    if key is not None and key.lower() in _SENSITIVE_KEYS and value is not None:
        return "[redacted]"
    if isinstance(value, str):
        return _EMAIL.sub("[email]", _PHONE.sub("[phone]", value))
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


class AuditRecord(BaseModel):
    seq: int = 0
    tenant_id: uuid.UUID
    ts: datetime
    actor_type: str
    actor_id: str
    action: str
    entity: str
    entity_id: str | None = None
    payload_redacted: dict[str, Any]
    prev_hash: str = GENESIS
    hash: str = ""


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def compute_hash(prev_hash: str, record: AuditRecord) -> str:
    body = {
        "tenant_id": str(record.tenant_id),
        "ts": record.ts.astimezone(UTC).isoformat(),
        "actor_type": record.actor_type,
        "actor_id": record.actor_id,
        "action": record.action,
        "entity": record.entity,
        "entity_id": record.entity_id,
        "payload": record.payload_redacted,
    }
    return hashlib.sha256((prev_hash + canonical(body)).encode()).hexdigest()


def chain(records: Iterable[AuditRecord], prev_hash: str = GENESIS) -> list[AuditRecord]:
    out = []
    for r in records:
        h = compute_hash(prev_hash, r)
        out.append(r.model_copy(update={"prev_hash": prev_hash, "hash": h}))
        prev_hash = h
    return out


def verify_chain(records: Sequence[AuditRecord]) -> int | None:
    """Return the first sequence number whose link or content is wrong, or None if intact."""
    prev = GENESIS
    for r in records:
        if r.prev_hash != prev or compute_hash(prev, r) != r.hash:
            return r.seq
        prev = r.hash
    return None


async def append(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    actor_type: str,
    actor_id: str,
    action: str,
    entity: str,
    entity_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    """Append one event inside the caller's transaction."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"audit:{tenant_id}"}
    )
    last = (
        await session.execute(
            select(AuditEvent.hash)
            .where(AuditEvent.tenant_id == tenant_id)
            .order_by(AuditEvent.seq.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    record = AuditRecord(
        tenant_id=tenant_id,
        ts=datetime.now(UTC),
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        payload_redacted=redact(payload or {}),
    )
    prev = last or GENESIS
    row = AuditEvent(
        tenant_id=tenant_id,
        ts=record.ts,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        payload_redacted=record.payload_redacted,
        prev_hash=prev,
        hash=compute_hash(prev, record),
    )
    session.add(row)
    await session.flush()
    return row


def _to_record(row: AuditEvent) -> AuditRecord:
    return AuditRecord(
        seq=row.seq,
        tenant_id=row.tenant_id,
        ts=row.ts,
        actor_type=row.actor_type,
        actor_id=row.actor_id,
        action=row.action,
        entity=row.entity,
        entity_id=row.entity_id,
        payload_redacted=row.payload_redacted,
        prev_hash=row.prev_hash,
        hash=row.hash,
    )


async def verify_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> int | None:
    rows = (
        await session.execute(
            select(AuditEvent).where(AuditEvent.tenant_id == tenant_id).order_by(AuditEvent.seq)
        )
    ).scalars()
    return verify_chain([_to_record(r) for r in rows])


async def verify_all(session: AsyncSession) -> dict[uuid.UUID, int | None]:
    tenants = (await session.execute(select(AuditEvent.tenant_id).distinct())).scalars().all()
    return {t: await verify_tenant(session, t) for t in tenants}

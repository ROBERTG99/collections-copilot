"""Ingest service: parse, validate every row, then upsert in one transaction (FR-1)."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from collections_copilot.db import audit
from collections_copilot.db.models import IngestBatch
from collections_copilot.db.repositories import Repos
from collections_copilot.ingest.schemas import CSV_COLUMNS, AgingRow


class TooManyRowsError(Exception):
    pass


@dataclass
class ParseResult:
    rows: list[AgingRow] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_rows(rows: list[dict[str, Any]]) -> str:
    return sha256_bytes(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode())


def validate_rows(raw_rows: list[dict[str, Any]], max_rows: int) -> ParseResult:
    if len(raw_rows) > max_rows:
        raise TooManyRowsError(f"{len(raw_rows)} rows > {max_rows}")
    result = ParseResult()
    for i, raw in enumerate(raw_rows, start=1):
        cleaned = {k: (None if v == "" else v) for k, v in raw.items() if k is not None}
        try:
            result.rows.append(AgingRow.model_validate(cleaned))
        except ValidationError as exc:
            for err in exc.errors():
                result.errors.append(
                    {"row": i, "field": ".".join(str(p) for p in err["loc"]), "message": err["msg"]}
                )
    return result


def parse_csv(data: bytes, max_rows: int) -> ParseResult:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return ParseResult(errors=[{"row": 0, "field": "file", "message": "file is not UTF-8"}])
    reader = csv.DictReader(io.StringIO(text))
    missing = [
        c
        for c in CSV_COLUMNS
        if c not in (reader.fieldnames or []) and c not in ("dispute_reason", "note", "status")
    ]
    if missing:
        return ParseResult(
            errors=[{"row": 0, "field": "header", "message": f"missing columns: {missing}"}]
        )
    return validate_rows(list(reader), max_rows)


async def apply_rows(repos: Repos, tenant_id: uuid.UUID, rows: list[AgingRow]) -> None:
    accounts: dict[str, uuid.UUID] = {}
    # Stable lock order across concurrent ingests that touch the same accounts (no deadlocks).
    for row in sorted(rows, key=lambda r: (r.external_ref, r.invoice_number)):
        if row.external_ref not in accounts:
            acc = await repos.accounts.upsert(
                tenant_id,
                {
                    "external_ref": row.external_ref,
                    "name": row.name,
                    "phone_e164": row.phone_e164,
                    "timezone": row.timezone,
                    "segment": row.segment,
                    "language": row.language,
                    "currency": row.currency,
                },
            )
            accounts[row.external_ref] = acc.id
        account_id = accounts[row.external_ref]
        await repos.invoices.upsert(
            tenant_id,
            account_id,
            {
                "number": row.invoice_number,
                "amount_cents": row.amount_cents,
                "currency": row.currency,
                "issued_on": row.issued_on,
                "due_on": row.due_on,
                "status": row.status,
                "dispute_reason": row.dispute_reason,
            },
        )
        if row.note:
            await repos.notes.add(tenant_id, account_id, "ingest", row.note)


async def ingest(
    repos: Repos,
    tenant_id: uuid.UUID,
    *,
    sha256: str,
    parsed: ParseResult,
    source: str,
    actor: str,
    object_key: str | None = None,
    filename: str | None = None,
    size_bytes: int | None = None,
    batch: IngestBatch | None = None,
) -> tuple[IngestBatch, bool]:
    """Returns (batch, duplicate). A duplicate returns the original batch untouched."""
    existing = await repos.ingest.by_sha(tenant_id, sha256)
    if existing is not None:
        if batch is not None and batch.id != existing.id:
            await repos.ingest.delete(tenant_id, batch.id)
        return existing, True
    try:
        # Claim the file hash first; a concurrent ingest of the same bytes blocks on the unique
        # index and then sees the winner's batch as a duplicate.
        async with repos.session.begin_nested():
            if batch is None:
                batch = await repos.ingest.create(
                    tenant_id,
                    source=source,
                    status="awaiting_upload",
                    created_by=actor,
                    filename=filename,
                    sha256=sha256,
                )
            else:
                batch.sha256 = sha256
                await repos.session.flush()
    except IntegrityError:
        winner = await repos.ingest.by_sha(tenant_id, sha256)
        if winner is None:
            raise
        if batch is not None and batch.id != winner.id:
            await repos.ingest.delete(tenant_id, batch.id)
        return winner, True
    batch.object_key = object_key or batch.object_key
    batch.size_bytes = size_bytes
    batch.row_count = len(parsed.rows) + len({e["row"] for e in parsed.errors})
    if parsed.errors:
        batch.status = "rejected"
        batch.errors = {"rows": parsed.errors[:500], "error_count": len(parsed.errors)}
    else:
        await apply_rows(repos, tenant_id, parsed.rows)
        batch.status = "completed"
        batch.errors = {}
    await repos.session.flush()
    await audit.append(
        repos.session,
        tenant_id,
        actor_type="user",
        actor_id=actor,
        action=f"ingest.{batch.status}",
        entity="ingest_batch",
        entity_id=str(batch.id),
        payload={"rows": batch.row_count, "source": source, "errors": len(parsed.errors)},
    )
    return batch, False

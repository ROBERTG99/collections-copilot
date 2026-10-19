"""T-12: ingest is idempotent by SHA-256 and rejects invalid rows with their row number (FR-1)."""

from __future__ import annotations

import csv
import io
import uuid
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.integration


def aging_rows(n: int, prefix: str = "X") -> list[dict[str, Any]]:
    rows = []
    for i in range(n):
        rows.append(
            {
                "external_ref": f"{prefix}{i // 2:04d}",
                "name": f"Synthetic Account {i // 2}",
                "phone_e164": f"+1555{i // 2:07d}",
                "timezone": "America/Chicago",
                "segment": "smb",
                "language": "en",
                "currency": "USD",
                "invoice_number": f"{prefix}-INV-{i:05d}",
                "amount": f"{100 + i}.25",
                "issued_on": "2026-07-01",
                "due_on": "2026-08-01",
                "status": "open",
                "dispute_reason": "",
                "note": "imported from aging report" if i % 10 == 0 else "",
            }
        )
    return rows


def to_csv(rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode()


async def _upload(
    client: httpx.AsyncClient, headers: dict[str, str], data: bytes
) -> httpx.Response:
    created = await client.post(
        "/api/v1/ingest/uploads",
        json={"filename": "aging.csv", "size_bytes": len(data)},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    async with httpx.AsyncClient() as s3:
        put = await s3.put(body["put_url"], content=data, headers={"Content-Type": "text/csv"})
        assert put.status_code == 200, put.text
    return await client.post(
        f"/api/v1/ingest/uploads/{body['upload_id']}:complete", headers=headers
    )


async def test_duplicate_file_returns_original_batch(client: httpx.AsyncClient, auth) -> None:  # type: ignore[no-untyped-def]
    headers = auth("analyst")
    data = to_csv(aging_rows(400, prefix=f"D{uuid.uuid4().hex[:4]}"))
    first = await _upload(client, headers, data)
    assert first.status_code == 200, first.text
    assert first.json()["duplicate"] is False
    assert first.json()["status"] == "completed"
    second = await _upload(client, headers, data)
    assert second.status_code == 200
    assert second.json() == {**first.json(), "duplicate": True}
    batch = await client.get(f"/api/v1/ingest/batches/{first.json()['batch_id']}", headers=headers)
    assert batch.json()["row_count"] == 400


async def test_negative_amount_rejected_with_row(client: httpx.AsyncClient, auth) -> None:  # type: ignore[no-untyped-def]
    headers = auth("analyst")
    rows = aging_rows(5, prefix=f"N{uuid.uuid4().hex[:4]}")
    rows[3]["amount"] = "-25.00"
    resp = await client.post("/api/v1/ingest/aging", json={"rows": rows}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rejected"
    batch = (
        await client.get(f"/api/v1/ingest/batches/{resp.json()['batch_id']}", headers=headers)
    ).json()
    assert batch["errors"]["rows"][0]["row"] == 4
    assert batch["errors"]["rows"][0]["field"] == "amount"
    again = await client.post("/api/v1/ingest/aging", json={"rows": rows}, headers=headers)
    assert again.json()["duplicate"] is True


async def test_json_ingest_upserts_accounts(client: httpx.AsyncClient, auth) -> None:  # type: ignore[no-untyped-def]
    headers = auth("analyst")
    rows = aging_rows(6, prefix=f"J{uuid.uuid4().hex[:4]}")
    resp = await client.post("/api/v1/ingest/aging", json={"rows": rows}, headers=headers)
    assert resp.json()["status"] == "completed"
    assert resp.json()["duplicate"] is False


async def test_upload_over_limit_413(client: httpx.AsyncClient, auth) -> None:  # type: ignore[no-untyped-def]
    resp = await client.post(
        "/api/v1/ingest/uploads",
        json={"filename": "big.csv", "size_bytes": 10 * 1024 * 1024 + 1},
        headers=auth("analyst"),
    )
    assert resp.status_code == 413
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["correlation_id"]


async def test_cross_tenant_batch_is_404(client: httpx.AsyncClient, auth, sessionmaker) -> None:  # type: ignore[no-untyped-def]
    from tests.factories import make_tenant

    rows = aging_rows(2, prefix=f"T{uuid.uuid4().hex[:4]}")
    resp = await client.post("/api/v1/ingest/aging", json={"rows": rows}, headers=auth("analyst"))
    other = await make_tenant(sessionmaker)
    got = await client.get(
        f"/api/v1/ingest/batches/{resp.json()['batch_id']}",
        headers=auth("analyst", tenant_id=other),
    )
    assert got.status_code == 404


async def test_concurrent_identical_ingests_one_batch(client: httpx.AsyncClient, auth) -> None:  # type: ignore[no-untyped-def]
    import asyncio

    headers = auth("analyst")
    rows = aging_rows(30, prefix=f"C{uuid.uuid4().hex[:4]}")
    results = await asyncio.gather(
        *[
            client.post("/api/v1/ingest/aging", json={"rows": rows}, headers=headers)
            for _ in range(4)
        ]
    )
    assert [r.status_code for r in results] == [200, 200, 200, 200], [r.text for r in results]
    bodies = [r.json() for r in results]
    assert len({b["batch_id"] for b in bodies}) == 1
    assert sum(1 for b in bodies if not b["duplicate"]) == 1


async def test_reingest_keeps_disputed_invoice(
    client: httpx.AsyncClient, auth, sessionmaker, tenant
) -> None:  # type: ignore[no-untyped-def]
    from collections_copilot.db.repositories import Repos

    headers = auth("analyst")
    rows = aging_rows(2, prefix=f"R{uuid.uuid4().hex[:4]}")
    first = await client.post("/api/v1/ingest/aging", json={"rows": rows}, headers=headers)
    assert first.json()["status"] == "completed"
    async with sessionmaker() as s, s.begin():
        repos = Repos(s)
        acc = await repos.accounts.get_by_ref(tenant, rows[0]["external_ref"])
        assert acc is not None
        await repos.invoices.mark_disputed(tenant, acc.id, "damaged goods")
    rows[0]["note"] = "re-sent aging"  # change the payload so it is not a duplicate
    again = await client.post("/api/v1/ingest/aging", json={"rows": rows}, headers=headers)
    assert again.json()["duplicate"] is False
    async with sessionmaker() as s:
        inv = await Repos(s).invoices.get_by_number(tenant, rows[0]["invoice_number"])
    assert inv is not None
    assert inv.status == "disputed"

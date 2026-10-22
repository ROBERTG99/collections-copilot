"""FR-2, FR-3, FR-4: queue ordering and cursor, account detail, case creation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from collections_copilot.api.routers.accounts import ACCOUNT_HISTORY_LIMIT, ACCOUNT_ITEMS_LIMIT
from collections_copilot.db.repositories import Repos
from collections_copilot.scoring.priority import inputs_from, score
from tests.factories import make_account, make_tenant

pytestmark = pytest.mark.integration


async def _seed(sessionmaker: Any, tenant: uuid.UUID, n: int = 7) -> list[uuid.UUID]:
    ids = []
    for i in range(n):
        ids.append(
            await make_account(
                sessionmaker,
                tenant,
                invoices=[{"amount_cents": 10_000 * (i + 1) ** 2, "days_past_due": 10 * i}],
                segment=("enterprise", "mid_market", "smb")[i % 3],
            )
        )
    return ids


async def _walk(client: httpx.AsyncClient, headers: dict[str, str], **params: Any) -> list[Any]:
    items: list[Any] = []
    cursor = None
    pages = 0
    while True:
        query = {**params, **({"cursor": cursor} if cursor else {})}
        resp = await client.get("/api/v1/queue", params=query, headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        items.extend(body["items"])
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            return items
        assert pages < 50


async def test_sorted_by_priority_with_cursor(
    client: httpx.AsyncClient, sessionmaker: Any, tenant: uuid.UUID, auth: Any
) -> None:
    await _seed(sessionmaker, tenant)
    other = await make_tenant(sessionmaker)
    await make_account(sessionmaker, other, invoices=[{"amount_cents": 9_000_000}])

    items = await _walk(client, auth("analyst"), limit=2)
    assert len(items) == 7  # the other tenant's account never appears
    keys = [(-i["priority_score"], i["account_id"]) for i in items]
    assert keys == sorted(keys)
    assert len({i["account_id"] for i in items}) == 7
    assert items[0]["priority_score"] > items[-1]["priority_score"]

    # The cached score is the deterministic formula.
    top = items[0]
    expected = score(
        inputs_from(
            invoices=[(top["open_balance"]["amount_cents"], "open", top["max_days_past_due"])],
            broken_promises_90d=0,
            segment=top["segment"],
            currency=top["open_balance"]["currency"],
        )
    )
    assert top["priority_score"] == expected.score

    first_page = await client.get("/api/v1/queue", params={"limit": 3}, headers=auth("analyst"))
    assert [i["account_id"] for i in first_page.json()["items"]] == [
        i["account_id"] for i in items[:3]
    ]


async def test_filters_and_bad_cursor(
    client: httpx.AsyncClient, sessionmaker: Any, tenant: uuid.UUID, auth: Any
) -> None:
    await _seed(sessionmaker, tenant)
    enterprise = await _walk(client, auth("analyst"), segment="enterprise")
    assert enterprise
    assert all(i["segment"] == "enterprise" for i in enterprise)
    high = await _walk(client, auth("analyst"), min_score=50)
    assert all(i["priority_score"] >= 50 for i in high)
    resp = await client.get("/api/v1/queue", params={"cursor": "%%%"}, headers=auth("analyst"))
    assert resp.status_code == 400
    assert resp.headers["content-type"].startswith("application/problem+json")
    resp = await client.get("/api/v1/queue", params={"limit": 101}, headers=auth("analyst"))
    assert resp.status_code == 422


async def test_account_detail_and_tenant_isolation(
    client: httpx.AsyncClient, sessionmaker: Any, tenant: uuid.UUID, auth: Any
) -> None:
    aid = await make_account(
        sessionmaker,
        tenant,
        invoices=[
            {"amount_cents": 50_000, "days_past_due": 45},
            {
                "amount_cents": 20_000,
                "days_past_due": 100,
                "status": "disputed",
                "dispute_reason": "short shipment",
            },
        ],
        notes=["Prefers SMS after 10:00."],
    )
    resp = await client.get(f"/api/v1/accounts/{aid}", headers=auth("analyst"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["open_balance"] == {"amount_cents": 50_000, "currency": "USD"}
    assert body["aging"]["d31_60"] == 50_000
    assert body["aging"]["d90_plus"] == 0
    assert {i["status"] for i in body["invoices"]} == {"open", "disputed"}
    assert body["notes"][0]["body"] == "Prefers SMS after 10:00."
    assert body["precheck"]["ok"] is True

    other = await make_tenant(sessionmaker)
    resp = await client.get(f"/api/v1/accounts/{aid}", headers=auth("analyst", tenant_id=other))
    assert resp.status_code == 404


async def test_account_detail_bounds_the_case_history(
    client: httpx.AsyncClient, sessionmaker: Any, tenant: uuid.UUID, auth: Any
) -> None:
    """NFR-1: the detail payload must not grow with years of history; the open case is the
    newest (one open case per account), so it is always in the window."""
    aid = await make_account(sessionmaker, tenant, invoices=[{"amount_cents": 5_000}])
    start = datetime(2026, 1, 1, tzinfo=UTC)
    async with sessionmaker() as s, s.begin():
        cases = Repos(s).cases
        for day in range(30):
            await cases.create(tenant, aid, status="closed", created_at=start + timedelta(days=day))
        open_case = await cases.create(
            tenant, aid, status="awaiting_approval", created_at=start + timedelta(days=40)
        )
    resp = await client.get(f"/api/v1/accounts/{aid}", headers=auth("analyst"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["cases"]) == ACCOUNT_HISTORY_LIMIT == 20
    assert body["cases_total"] == 31
    assert body["cases"][0]["id"] == str(open_case.id)
    created = [c["created_at"] for c in body["cases"]]
    assert created == sorted(created, reverse=True)


async def test_account_detail_bounds_promises(
    client: httpx.AsyncClient, sessionmaker: Any, tenant: uuid.UUID, auth: Any
) -> None:
    """Promises grew without bound like cases did; the newest 50 are returned."""
    aid = await make_account(sessionmaker, tenant, invoices=[{"amount_cents": 5_000}])
    start = datetime(2026, 1, 1, tzinfo=UTC)
    async with sessionmaker() as s, s.begin():
        promises = Repos(s).promises
        for day in range(ACCOUNT_ITEMS_LIMIT + 5):
            await promises.create_if_absent(
                tenant,
                account_id=aid,
                amount_cents=1_000,
                currency="USD",
                promised_date=(start + timedelta(days=day)).date(),
                invoice_numbers=[],
                status="broken",
                idempotency_key=f"ptp:{aid}:{day}",
                created_at=start + timedelta(days=day),
            )
    resp = await client.get(f"/api/v1/accounts/{aid}", headers=auth("analyst"))
    assert resp.status_code == 200, resp.text
    dates = [p["promised_date"] for p in resp.json()["promises"]]
    assert len(dates) == ACCOUNT_ITEMS_LIMIT == 50
    assert dates[0] == (start + timedelta(days=ACCOUNT_ITEMS_LIMIT + 4)).date().isoformat()


async def test_create_cases_enqueues_and_dedupes(
    client: httpx.AsyncClient, api_app: Any, sessionmaker: Any, tenant: uuid.UUID, auth: Any
) -> None:
    sent: list[dict[str, Any]] = []

    class Recorder:
        async def send(self, queue: str, body: dict[str, Any], **_: Any) -> str:
            sent.append(body)
            return "m"

    real = api_app.state.queues
    api_app.state.queues = Recorder()
    try:
        a, b = await _seed(sessionmaker, tenant, n=2)
        resp = await client.post(
            "/api/v1/cases", json={"account_ids": [str(a), str(b)]}, headers=auth("analyst")
        )
        assert resp.status_code == 202, resp.text
        cases = resp.json()["cases"]
        assert [c["existing"] for c in cases] == [False, False]
        assert {m["case_id"] for m in sent} == {c["id"] for c in cases}
        assert all(m["kind"] == "process_case" and m["tenant_id"] == str(tenant) for m in sent)

        detail = await client.get(f"/api/v1/cases/{cases[0]['id']}", headers=auth("analyst"))
        assert detail.status_code == 200
        assert detail.json()["case"]["status"] == "queued"

        other = await make_tenant(sessionmaker)
        hidden = await client.get(
            f"/api/v1/cases/{cases[0]['id']}", headers=auth("analyst", tenant_id=other)
        )
        assert hidden.status_code == 404
        foreign = await client.post(
            "/api/v1/cases",
            json={"account_ids": [str(a)]},
            headers=auth("analyst", tenant_id=other),
        )
        assert foreign.status_code == 404
    finally:
        api_app.state.queues = real


async def test_awaiting_approval_rows_carry_the_draft_for_batch_review(  # type: ignore[no-untyped-def]
    client: httpx.AsyncClient, sessionmaker, tenant, auth
) -> None:
    """Batch approve shows each exact SMS, so the queue carries the latest draft."""
    from collections_copilot.db.repositories import Repos

    account = await make_account(sessionmaker, tenant)
    async with sessionmaker() as s, s.begin():
        repos = Repos(s)
        case = await repos.cases.create(tenant, account, status="awaiting_approval")
        await repos.drafts.add(tenant, case.id, version=1, body="v1", language="en", author="ai")
        await repos.drafts.add(
            tenant, case.id, version=2, body="v2 text", language="en", author="ai"
        )
    page = (
        await client.get(
            "/api/v1/queue", params={"status": "awaiting_approval"}, headers=auth("analyst")
        )
    ).json()
    item = next(i for i in page["items"] if i["account_id"] == str(account))
    assert (item["draft_version"], item["draft_body"]) == (2, "v2 text")
    assert item["timezone"]
    other = await make_account(sessionmaker, tenant)
    plain = (await client.get("/api/v1/queue", params={"q": ""}, headers=auth("analyst"))).json()
    row = next((i for i in plain["items"] if i["account_id"] == str(other)), None)
    assert row is None or row["draft_version"] is None

"""FR-12, FR-14: ops metrics equal the seeded counts; the audit endpoint pages and verifies."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from collections_copilot.db import audit
from collections_copilot.db.repositories import Repos
from tests.factories import make_account

pytestmark = pytest.mark.integration
NOW = datetime.now(UTC)
# 15:00 UTC is inside the window in New York (10:00/11:00) and outside it in Tokyo (00:00).
AT_15 = (NOW - timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)


class DepthQueue:
    async def depth(self, queue: str) -> int:
        if "replies" in queue:
            raise RuntimeError("queue down")
        return 2


@pytest.fixture
async def depth_queue(api_app: Any) -> AsyncIterator[None]:
    saved = api_app.state.queues
    api_app.state.queues = DepthQueue()
    try:
        yield
    finally:
        api_app.state.queues = saved


async def seed(sm: Any, tenant: uuid.UUID) -> None:
    ny = await make_account(sm, tenant)
    tokyo = await make_account(sm, tenant, timezone="Asia/Tokyo")
    async with sm() as s, s.begin():
        repos = Repos(s)
        cases = [
            await repos.cases.create(tenant, ny, status="closed"),
            await repos.cases.create(tenant, ny, status="awaiting_approval"),
            await repos.cases.create(tenant, tokyo, status="awaiting_approval"),
        ]
        for n, (account, case) in enumerate(((ny, cases[0]), (ny, cases[1]), (tokyo, cases[2]))):
            await repos.messages.claim(
                tenant,
                f"sms:{case.id}:{n}",
                account_id=account,
                case_id=case.id,
                direction="outbound",
                to_e164="+15550000001",
                body="x",
                status="delivered",
                sent_at=AT_15,
            )
        await repos.messages.add_inbound(
            tenant,
            provider_sid="SM" + uuid.uuid4().hex,
            account_id=ny,
            from_e164="+15550000001",
            body="paid",
            status="handled",
        )
        for cost, kind, node, latency in [
            (0.01, "llm", "draft", 900.0),
            (0.02, "llm", "next_best_action", 1500.0),
            *[(0.0, "node", "draft", float(ms)) for ms in range(10, 101, 10)],
            (0.0, "node", "human_approval", 5.0),
        ]:
            await repos.steps.add(
                tenant,
                case_id=cases[1].id,
                run_id="r",
                task_id="t",
                step=0,
                node=node,
                kind=kind,
                cost_usd=cost,
                latency_ms=latency,
                status="ok",
                started_at=NOW,
            )
        for status in ("recorded", "broken"):
            await repos.promises.create_if_absent(
                tenant,
                account_id=ny,
                amount_cents=100,
                currency="USD",
                promised_date=NOW.date(),
                invoice_numbers=["INV-1"],
                status=status,
                idempotency_key=f"ptp:{tenant}:{uuid.uuid4().hex}",
            )
        await repos.opt_outs.add(tenant, "+15550009999", source="sms_stop")
        await repos.follow_ups.add(
            tenant,
            case_id=cases[1].id,
            account_id=ny,
            kind="approval_reminder",
            due_at=NOW + timedelta(hours=24),
        )


async def test_numbers_match_seeded_counts(
    client: httpx.AsyncClient, sessionmaker, tenant, auth, depth_queue
) -> None:  # type: ignore[no-untyped-def]
    await seed(sessionmaker, tenant)
    resp = await client.get("/api/v1/metrics/ops", headers=auth("supervisor"))
    assert resp.status_code == 200, resp.text
    m = resp.json()
    assert m["cases_by_status"] == {"closed": 1, "awaiting_approval": 2}
    assert m["pending_approvals"] == 2
    assert m["promises_by_status"] == {"recorded": 1, "broken": 1}
    assert m["sends"] == 3
    assert m["contact_window_rate"] == round(2 / 3, 4)
    assert m["llm_cost_usd"] == pytest.approx(0.03)
    assert m["accounts_with_cases"] == 2
    assert m["cost_per_account_usd"] == pytest.approx(0.015)
    assert m["node_p95_ms"] == {"draft": 95.5, "human_approval": 5.0}
    assert m["dlq_depth"] == {"cc-tasks-dlq": 2, "cc-replies-dlq": None}
    assert (m["opt_outs"], m["follow_ups_pending"], m["replies"]) == (1, 1, 1)

    other = await client.get(
        "/api/v1/metrics/ops", headers=auth("supervisor", tenant_id=uuid.uuid4())
    )
    assert other.json()["cases_by_status"] == {}


async def test_audit_pages_and_verifies(
    client: httpx.AsyncClient, sessionmaker, tenant, auth
) -> None:  # type: ignore[no-untyped-def]
    async with sessionmaker() as s, s.begin():
        for n in range(5):
            await audit.append(
                s,
                tenant,
                actor_type="user",
                actor_id="ana",
                action="approval.approve",
                entity="case",
                entity_id=f"c{n}",
                payload={"n": n, "phone_e164": "+15550000001"},
            )
    first = await client.get(
        "/api/v1/audit", params={"limit": 3, "verify": "true"}, headers=auth("supervisor")
    )
    assert first.status_code == 200, first.text
    page = first.json()
    assert page["chain_ok"] is True
    assert [e["entity_id"] for e in page["items"]] == ["c4", "c3", "c2"]
    assert page["items"][0]["payload"]["phone_e164"] != "+15550000001"  # stored redacted
    rest = await client.get(
        "/api/v1/audit", params={"before_seq": page["next_before_seq"]}, headers=auth("supervisor")
    )
    assert [e["entity_id"] for e in rest.json()["items"]] == ["c1", "c0"]
    assert (await client.get("/api/v1/audit", headers=auth("analyst"))).status_code == 403


async def test_manual_opt_out_closes_open_work(
    client: httpx.AsyncClient, sessionmaker, tenant, auth
) -> None:  # type: ignore[no-untyped-def]
    account = await make_account(sessionmaker, tenant)
    async with sessionmaker() as s, s.begin():
        repos = Repos(s)
        acc = await repos.accounts.get(tenant, account)
        assert acc is not None
        phone = acc.phone_e164
        case = await repos.cases.create(tenant, account, status="awaiting_approval")
    body = {"phone_e164": phone, "reason": "asked by phone"}
    first = await client.post("/api/v1/opt-outs", json=body, headers=auth("analyst"))
    assert first.status_code == 201, first.text
    assert (first.json()["created"], first.json()["cases_closed"]) == (True, 1)
    again = await client.post("/api/v1/opt-outs", json=body, headers=auth("analyst"))
    assert again.json()["created"] is False
    bad = await client.post("/api/v1/opt-outs", json={"phone_e164": "555"}, headers=auth("analyst"))
    assert bad.status_code == 422
    listed = await client.get("/api/v1/opt-outs", headers=auth("admin"))
    assert phone in [o["phone_e164"] for o in listed.json()]
    async with sessionmaker() as s:
        closed = await Repos(s).cases.get(tenant, case.id)
    assert closed is not None
    assert closed.status == "closed"

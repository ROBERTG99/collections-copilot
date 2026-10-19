"""T-27 / FR-6: approve, edit (diff stored, post-check on the edited text) and reject through
the API, then the worker resumes from the enqueued task."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from collections_copilot.db.models import Approval, AuditEvent
from collections_copilot.db.repositories import Repos
from collections_copilot.integrations.queue.messages import TaskMessage
from collections_copilot.integrations.queue.sqs import QueueUnavailableError
from collections_copilot.integrations.sms.base import InMemorySmsSender
from collections_copilot.worker.runner import CaseRunner
from tests.factories import make_account
from tests.integration.graph_support import case_graph, config, make_services, new_case

pytestmark = pytest.mark.integration
PLAN_NOTE = "Owner asked to split the balance into installments; cash flow is tight this quarter."


class RecordingQueue:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.down = False

    async def send(self, queue: str, body: dict[str, Any], **_: Any) -> str:
        if self.down:
            raise QueueUnavailableError("down")
        self.sent.append(body)
        return "m"


@pytest.fixture
async def queue(api_app: Any) -> AsyncIterator[RecordingQueue]:
    real = api_app.state.queues
    api_app.state.queues = RecordingQueue()
    try:
        yield api_app.state.queues
    finally:
        api_app.state.queues = real


async def paused_case(sm: Any, database_url: str, tenant: uuid.UUID, sms: Any) -> tuple[str, Any]:
    account = await make_account(sm, tenant, notes=[PLAN_NOTE])
    case_id = await new_case(sm, tenant, account)
    async with case_graph(make_services(sm, database_url, sms=sms), database_url) as graph:
        await graph.ainvoke({"case_id": case_id, "tenant_id": str(tenant)}, config(case_id))
        snapshot = await graph.aget_state(config(case_id))
    assert snapshot.interrupts[0].value["kind"] == "approval"
    return case_id, snapshot.values["draft"]


async def run_worker(
    sm: Any, database_url: str, sms: Any, tasks: list[dict[str, Any]]
) -> list[str]:
    async with case_graph(make_services(sm, database_url, sms=sms), database_url) as graph:
        runner = CaseRunner(graph)
        return [await runner.handle(TaskMessage.model_validate(t)) for t in tasks]


async def test_edit_stores_diff_and_rechecks(
    client: httpx.AsyncClient, sessionmaker, database_url, tenant, auth, queue
) -> None:  # type: ignore[no-untyped-def]
    sms = InMemorySmsSender()
    case_id, draft = await paused_case(sessionmaker, database_url, tenant, sms)
    url = f"/api/v1/cases/{case_id}/approval"

    # An edit that introduces an amount not in the ledger is blocked; nothing is stored.
    bad = draft["body"].replace("Reply STOP", "Pay USD 999.00 today. Reply STOP")
    resp = await client.post(
        url, json={"decision": "edit", "draft_version": 1, "body": bad}, headers=auth("analyst")
    )
    assert resp.status_code == 422, resp.text
    codes = [v["code"] for v in resp.json()["postcheck"]["violations"] if v["blocking"]]
    assert "amount_not_in_ledger" in codes
    assert queue.sent == []

    edited = draft["body"].replace("Hi ", "Hello ", 1)
    assert edited != draft["body"]
    resp = await client.post(
        url, json={"decision": "edit", "draft_version": 1, "body": edited}, headers=auth("analyst")
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["draft_version"] == 2
    assert out["postcheck"]["ok"] is True
    assert "-" + draft["body"] in out["diff"]
    assert "+" + edited in out["diff"]

    async with sessionmaker() as s:
        repos = Repos(s)
        drafts = await repos.drafts.for_case(tenant, uuid.UUID(case_id))
        assert [(d.version, d.author, d.status) for d in drafts] == [
            (1, drafts[0].author, "superseded"),
            (2, "analyst", "approved"),
        ]
        [approval] = (
            (await s.execute(select(Approval).where(Approval.case_id == uuid.UUID(case_id))))
            .scalars()
            .all()
        )
        assert approval.decision == "edit"
        assert approval.diff == out["diff"]
        events = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.entity_id == case_id, AuditEvent.action == "approval.edit"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert events[0].payload_redacted["diff"] == out["diff"]

    # A second decision on the old version is stale.
    stale = await client.post(
        url, json={"decision": "approve", "draft_version": 1}, headers=auth("analyst")
    )
    assert stale.status_code == 409

    # The worker resumes from the enqueued task and sends exactly the edited text.
    assert [t["resume"]["kind"] for t in queue.sent] == ["approval"]
    outcomes = await run_worker(sessionmaker, database_url, sms, queue.sent)
    assert outcomes == ["resumed"]
    assert [m["body"] for m in sms.sent] == [edited]


async def test_reject_needs_reason_and_closes(
    client: httpx.AsyncClient, sessionmaker, database_url, tenant, auth, queue
) -> None:  # type: ignore[no-untyped-def]
    sms = InMemorySmsSender()
    case_id, _ = await paused_case(sessionmaker, database_url, tenant, sms)
    url = f"/api/v1/cases/{case_id}/approval"
    missing = await client.post(
        url, json={"decision": "reject", "draft_version": 1}, headers=auth("analyst")
    )
    assert missing.status_code == 422
    resp = await client.post(
        url,
        json={"decision": "reject", "draft_version": 1, "reason": "tone"},
        headers=auth("supervisor"),
    )
    assert resp.status_code == 200, resp.text
    await run_worker(sessionmaker, database_url, sms, queue.sent)
    assert sms.sent == []
    async with sessionmaker() as s:
        case = await Repos(s).cases.get(tenant, case_id)
    assert case is not None
    assert case.status == "closed"


async def test_retry_after_queue_outage_replays(
    client: httpx.AsyncClient, sessionmaker, database_url, tenant, auth, queue
) -> None:  # type: ignore[no-untyped-def]
    sms = InMemorySmsSender()
    case_id, _ = await paused_case(sessionmaker, database_url, tenant, sms)
    url = f"/api/v1/cases/{case_id}/approval"
    queue.down = True
    first = await client.post(
        url, json={"decision": "approve", "draft_version": 1}, headers=auth("analyst")
    )
    assert first.status_code == 503
    assert first.headers["retry-after"] == "5"
    queue.down = False
    again = await client.post(
        url, json={"decision": "approve", "draft_version": 1}, headers=auth("analyst")
    )
    assert again.status_code == 200, again.text
    assert again.json()["replayed"] is True
    duplicate = await client.post(
        url, json={"decision": "approve", "draft_version": 1}, headers=auth("analyst")
    )
    assert duplicate.status_code == 200
    outcomes = await run_worker(sessionmaker, database_url, sms, queue.sent)
    assert outcomes[0] == "resumed"
    assert all(o in ("not_paused", "kind_mismatch") for o in outcomes[1:])
    assert len(sms.sent) == 1


async def test_batch_reports_per_item(
    client: httpx.AsyncClient, sessionmaker, database_url, tenant, auth, queue
) -> None:  # type: ignore[no-untyped-def]
    sms = InMemorySmsSender()
    case_a, _ = await paused_case(sessionmaker, database_url, tenant, sms)
    case_b, _ = await paused_case(sessionmaker, database_url, tenant, sms)
    resp = await client.post(
        "/api/v1/approvals:batch",
        json={
            "items": [
                {"case_id": case_a, "decision": "approve", "draft_version": 1},
                {"case_id": case_b, "decision": "approve", "draft_version": 7},
                {"case_id": str(uuid.uuid4()), "decision": "approve", "draft_version": 1},
            ]
        },
        headers=auth("analyst"),
    )
    assert resp.status_code == 200, resp.text
    assert [r["status"] for r in resp.json()["results"]] == [200, 409, 404]
    too_many = await client.post(
        "/api/v1/approvals:batch",
        json={"items": [{"case_id": case_a, "decision": "approve", "draft_version": 1}] * 51},
        headers=auth("analyst"),
    )
    assert too_many.status_code == 422


async def test_auto_approval_settings_only_tighten(client: httpx.AsyncClient, tenant, auth) -> None:  # type: ignore[no-untyped-def]
    url = "/api/v1/settings/auto-approval"
    analyst = await client.put(
        url, json={"enabled": True, "max_usd": 100, "min_confidence": 0.95}, headers=auth("analyst")
    )
    assert analyst.status_code == 403
    loosen = await client.put(
        url,
        json={"enabled": True, "max_usd": 900, "min_confidence": 0.95},
        headers=auth("supervisor"),
    )
    assert loosen.status_code == 422
    ok = await client.put(
        url,
        json={"enabled": True, "max_usd": 100, "min_confidence": 0.95},
        headers=auth("supervisor"),
    )
    assert ok.status_code == 200, ok.text
    got = (await client.get(url, headers=auth("analyst"))).json()
    assert (got["max_usd"], got["min_confidence"], got["ceiling_max_usd"]) == (100, 0.95, 500)


async def test_live_postcheck_stores_nothing(
    client: httpx.AsyncClient, sessionmaker, database_url, tenant, auth, queue
) -> None:  # type: ignore[no-untyped-def]
    sms = InMemorySmsSender()
    case_id, draft = await paused_case(sessionmaker, database_url, tenant, sms)
    url = f"/api/v1/cases/{case_id}/postcheck"
    good = await client.post(url, json={"body": draft["body"]}, headers=auth("analyst"))
    assert good.status_code == 200, good.text
    assert good.json()["ok"] is True
    bad = await client.post(
        url, json={"body": "Pay USD 999.00 now or else."}, headers=auth("analyst")
    )
    codes = {v["code"] for v in bad.json()["violations"] if v["blocking"]}
    assert {"amount_not_in_ledger", "missing_footer"} <= codes
    async with sessionmaker() as s:
        drafts = await Repos(s).drafts.for_case(tenant, uuid.UUID(case_id))
    assert len(drafts) == 1
    assert queue.sent == []
    detail = await client.get(f"/api/v1/cases/{case_id}", headers=auth("analyst"))
    assert detail.json()["recipient_e164"].startswith("+1555")

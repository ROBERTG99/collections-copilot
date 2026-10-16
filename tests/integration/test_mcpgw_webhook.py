"""T-31 / FR-9: the gateway's approval webhook, and supervisor approval of a held promise."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from collections_copilot.agent.nodes.record_promise import retry_write_back
from collections_copilot.agent.reply_graph import build_reply_graph
from collections_copilot.db.repositories import Repos
from collections_copilot.integrations.email.smtp import InMemoryNotifier
from collections_copilot.integrations.mcp.admin import HttpGatewayAdmin
from collections_copilot.integrations.mcp.ledger import McpLedgerTools
from collections_copilot.integrations.mcp.tokens import DevIssuerTokenProvider
from collections_copilot.integrations.queue.messages import TaskMessage
from collections_copilot.worker.runner import CaseRunner
from tests.integration.test_approval_flow import RecordingQueue
from tests.integration.test_promise_writeback import ledger_rows, seeded_account, services_with
from tests.integration.test_reply_graph import inbound

pytestmark = pytest.mark.integration
WEBHOOK = "/api/v1/webhooks/mcpgw/approvals"


@pytest.fixture
async def wired(api_app: Any, stack: Any) -> AsyncIterator[tuple[RecordingQueue, InMemoryNotifier]]:
    saved = (api_app.state.queues, api_app.state.notifier, api_app.state.gateway_admin)
    api_app.state.queues = RecordingQueue()
    api_app.state.notifier = InMemoryNotifier()
    api_app.state.gateway_admin = HttpGatewayAdmin(
        stack.admin_url, DevIssuerTokenProvider(stack.issuer_url, subject="cc-api")
    )
    try:
        yield api_app.state.queues, api_app.state.notifier
    finally:
        api_app.state.queues, api_app.state.notifier, api_app.state.gateway_admin = saved


def signed(api_app: Any, payload: dict[str, Any], secret: str | None = None) -> dict[str, Any]:
    body = json.dumps(payload).encode()
    key = (secret or api_app.state.settings.mcpgw_webhook_secret).encode()
    sig = "sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest()
    return {
        "content": body,
        "headers": {"X-MCPGW-Signature": sig, "Content-Type": "application/json"},
    }


def approval_event(approval_id: str, tenant: uuid.UUID, sid: str = "") -> dict[str, Any]:
    return {
        "approval_id": approval_id,
        "tenant": str(tenant),
        "agent": "cc-worker",
        "tool": "ledger.record_promise_to_pay",
        "args_redacted": {"source_message_sid": sid} if sid else {},
        "reasons": ["amount above 500000 cents"],
        "expires_at": "2099-01-01T00:00:00+00:00",
    }


async def test_unknown_approval_404(client: httpx.AsyncClient, api_app, tenant, wired) -> None:  # type: ignore[no-untyped-def]
    event = approval_event(str(uuid.uuid4()), tenant)
    resp = await client.post(WEBHOOK, **signed(api_app, event))
    assert resp.status_code == 404
    forged = await client.post(WEBHOOK, **signed(api_app, event, secret="not-the-secret"))
    assert forged.status_code == 403
    assert wired[1].sent == []


async def test_supervisor_approval_enqueues_retry(
    client: httpx.AsyncClient,
    api_app,
    sessionmaker,
    database_url,
    migrated_database,
    tenant,
    stack,
    auth,
    wired,
) -> None:  # type: ignore[no-untyped-def]
    queue, notifier = wired
    account, _ = await seeded_account(sessionmaker, stack, tenant, 1_000_000)
    tools = McpLedgerTools(stack.gateway_url, DevIssuerTokenProvider(stack.issuer_url))
    services = services_with(sessionmaker, database_url, tools)
    reply = await inbound(sessionmaker, tenant, account, "I can pay 8,000 on Friday")
    result = await build_reply_graph(services, sessionmaker).ainvoke(
        {"reply": reply.model_dump(mode="json", by_alias=True)}
    )
    assert result["outcome"] == "promise_held"
    promise_id = result["promise_id"]
    async with sessionmaker() as s:
        promise = await Repos(s).promises.get(tenant, promise_id)
    assert promise is not None
    assert promise.mcp_approval_id

    # The gateway's webhook links the approval and emails a supervisor once.
    event = approval_event(promise.mcp_approval_id, tenant, reply.provider_sid)
    for _ in range(2):
        assert (await client.post(WEBHOOK, **signed(api_app, event))).status_code == 204
    assert [n["key"] for n in notifier.sent] == [f"mcpgw-approval:{promise.mcp_approval_id}"]

    url = f"/api/v1/promises/{promise_id}:approve"
    assert (await client.post(url, headers=auth("analyst"))).status_code == 403
    listed = await client.get(
        "/api/v1/promises", params={"status": "approval_required"}, headers=auth("analyst")
    )
    assert promise_id in [p["id"] for p in listed.json()]

    resp = await client.post(url, headers=auth("supervisor", username="sam.supervisor"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["gateway_status"] == "approved"
    again = await client.post(url, headers=auth("supervisor", username="sam.supervisor"))
    assert again.status_code == 200  # a retried request reads the gateway decision back
    assert [t["kind"] for t in queue.sent] == ["retry_write_back", "retry_write_back"]

    async def write_back(tenant_id: str, pid: str) -> str:
        return await retry_write_back(services, sessionmaker, tenant_id, pid)

    runner = CaseRunner(graph=None, write_back=write_back)
    outcomes = [await runner.handle(TaskMessage.model_validate(t)) for t in queue.sent]
    assert outcomes == ["promise_recorded", "promise_recorded"]
    assert ledger_rows(migrated_database, tenant) == [(promise.idempotency_key, 800_000)]
    async with sessionmaker() as s:
        promise = await Repos(s).promises.get(tenant, promise_id)
    assert promise is not None
    assert promise.decided_by == "sam.supervisor"

    rejected = await client.post(
        f"/api/v1/promises/{promise_id}:reject", json={"reason": "late"}, headers=auth("supervisor")
    )
    assert rejected.status_code == 409  # already recorded

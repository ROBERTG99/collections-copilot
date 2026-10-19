"""T-24 / FR-9: promise write-back through mcp-gateway with ptp:<tenant>:<MessageSid>."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

import httpx
import psycopg
import pytest

from collections_copilot.agent.nodes.record_promise import promise_key, retry_write_back
from collections_copilot.agent.reply_graph import build_reply_graph
from collections_copilot.db.repositories import Repos
from collections_copilot.integrations.mcp.ledger import LedgerResult, McpLedgerTools
from collections_copilot.integrations.mcp.tokens import DevIssuerTokenProvider
from tests.factories import make_account
from tests.integration.graph_support import make_services
from tests.integration.ledger_stack import LedgerStack
from tests.integration.test_reply_graph import inbound

pytestmark = pytest.mark.integration


async def seeded_account(
    sm: Any, stack: LedgerStack, tenant: uuid.UUID, amount_cents: int
) -> tuple[uuid.UUID, str]:
    number = f"INV-{uuid.uuid4().hex[:8]}"
    account = await make_account(
        sm, tenant, invoices=[{"amount_cents": amount_cents, "days_past_due": 40, "number": number}]
    )
    async with sm() as s:
        acc = await Repos(s).accounts.get(tenant, account)
        assert acc is not None
        ref = acc.external_ref
    await stack.store.upsert_account(str(tenant), ref, "Synthetic", "USD")
    await stack.store.upsert_invoice(
        str(tenant), ref, number, amount_cents, "USD", date.today() - timedelta(days=40)
    )
    return account, ref


def ledger_rows(db: str, tenant: uuid.UUID) -> list[tuple[str, int]]:
    with psycopg.connect(db) as conn:
        return conn.execute(
            "SELECT idempotency_key, amount_cents FROM ledger.promises WHERE tenant=%s",
            (str(tenant),),
        ).fetchall()


class CrashAfterCall:
    """The ledger call succeeds, then the worker dies before writing the result."""

    def __init__(self, inner: McpLedgerTools) -> None:
        self.inner = inner
        self.calls = 0

    async def record_promise_to_pay(
        self, tenant: str, args: dict[str, Any], **kw: Any
    ) -> LedgerResult:
        self.calls += 1
        result = await self.inner.record_promise_to_pay(tenant, args, **kw)
        if self.calls == 1:
            raise RuntimeError("worker died after the ledger call")
        return result

    async def get_balance(self, tenant: str, account_ref: str) -> dict[str, Any]:
        return await self.inner.get_balance(tenant, account_ref)


def services_with(sm: Any, database_url: str, ledger: Any) -> Any:
    services = make_services(sm, database_url)
    services.ledger = ledger
    return services


async def test_redelivery_single_ledger_row(
    sessionmaker, database_url, migrated_database, tenant, stack
) -> None:  # type: ignore[no-untyped-def]
    account, _ = await seeded_account(sessionmaker, stack, tenant, 120_000)
    tools = McpLedgerTools(stack.gateway_url, DevIssuerTokenProvider(stack.issuer_url))
    crashing = CrashAfterCall(tools)
    graph = build_reply_graph(services_with(sessionmaker, database_url, crashing), sessionmaker)
    reply = await inbound(sessionmaker, tenant, account, "I can pay 1,200 on Friday")
    payload = {"reply": reply.model_dump(mode="json", by_alias=True)}
    with pytest.raises(RuntimeError):
        await graph.ainvoke(payload)
    result = await graph.ainvoke(payload)  # SQS redelivery
    assert result["outcome"] == "promise_recorded"
    again = await graph.ainvoke(payload)  # a third delivery is a no-op (message handled)
    assert again["outcome"] == "duplicate"
    rows = ledger_rows(migrated_database, tenant)
    assert rows == [(promise_key(str(tenant), reply.provider_sid), 120_000)]
    async with sessionmaker() as s:
        promise = await Repos(s).promises.by_key(
            tenant, promise_key(str(tenant), reply.provider_sid)
        )
        assert promise is not None
        assert promise.status == "recorded"
        assert promise.ledger_ref


async def test_approval_required_then_retry_same_key(
    sessionmaker, database_url, migrated_database, tenant, stack
) -> None:  # type: ignore[no-untyped-def]
    account, _ = await seeded_account(sessionmaker, stack, tenant, 1_000_000)
    tools = McpLedgerTools(stack.gateway_url, DevIssuerTokenProvider(stack.issuer_url))
    services = services_with(sessionmaker, database_url, tools)
    graph = build_reply_graph(services, sessionmaker)
    reply = await inbound(sessionmaker, tenant, account, "I can pay 8,000 on Friday")
    result = await graph.ainvoke({"reply": reply.model_dump(mode="json", by_alias=True)})
    assert result["outcome"] == "promise_held"
    assert ledger_rows(migrated_database, tenant) == []
    async with sessionmaker() as s:
        promise = await Repos(s).promises.by_key(
            tenant, promise_key(str(tenant), reply.provider_sid)
        )
    assert promise is not None
    assert promise.status == "approval_required"
    assert promise.mcp_approval_id
    # The supervisor approves at the gateway (what POST /promises/{id}:approve does).
    async with httpx.AsyncClient() as client:
        tok = (
            await client.post(
                f"{stack.issuer_url}/token",
                json={"sub": "sam.supervisor", "tenant": str(tenant), "scopes": ["mcpgw:approve"]},
            )
        ).json()["access_token"]
        resp = await client.post(
            f"{stack.admin_url}/admin/approvals/{promise.mcp_approval_id}:approve",
            headers={"Authorization": f"Bearer {tok}"},
            json={"note": "ok"},
        )
        assert resp.status_code == 200, resp.text
    assert (
        await retry_write_back(services, sessionmaker, str(tenant), str(promise.id))
        == "promise_recorded"
    )
    assert (
        await retry_write_back(services, sessionmaker, str(tenant), str(promise.id))
        == "promise_recorded"
    )
    assert ledger_rows(migrated_database, tenant) == [(promise.idempotency_key, 800_000)]


async def test_idempotency_conflict_marks_failed(
    sessionmaker, database_url, migrated_database, tenant, stack
) -> None:  # type: ignore[no-untyped-def]
    account, ref = await seeded_account(sessionmaker, stack, tenant, 200_000)
    tools = McpLedgerTools(stack.gateway_url, DevIssuerTokenProvider(stack.issuer_url))
    reply = await inbound(sessionmaker, tenant, account, "I can pay 500 tomorrow")
    key = promise_key(str(tenant), reply.provider_sid)
    async with sessionmaker() as s:
        invoice = (await Repos(s).invoices.for_account(tenant, account))[0].number
    first = await tools.record_promise_to_pay(
        str(tenant),
        {
            "account_ref": ref,
            "amount_cents": 99_900,
            "currency": "USD",
            "promised_date": (date.today() + timedelta(days=5)).isoformat(),
            "invoice_numbers": [invoice],
            "source_message_sid": reply.provider_sid,
        },
        idempotency_key=key,
    )
    assert first.status == "recorded"
    counting = CountingLedger(tools)
    graph = build_reply_graph(services_with(sessionmaker, database_url, counting), sessionmaker)
    result = await graph.ainvoke({"reply": reply.model_dump(mode="json", by_alias=True)})
    assert result["outcome"] == "promise_failed"
    assert counting.calls == 1  # no retry after -32040
    async with sessionmaker() as s:
        promise = await Repos(s).promises.by_key(tenant, key)
    assert promise is not None
    assert promise.status == "failed"
    assert promise.failure_reason == "idempotency_conflict"
    assert len(ledger_rows(migrated_database, tenant)) == 1


class CountingLedger:
    def __init__(self, inner: McpLedgerTools) -> None:
        self.inner = inner
        self.calls = 0

    async def record_promise_to_pay(
        self, tenant: str, args: dict[str, Any], **kw: Any
    ) -> LedgerResult:
        self.calls += 1
        return await self.inner.record_promise_to_pay(tenant, args, **kw)

    async def get_balance(self, tenant: str, account_ref: str) -> dict[str, Any]:
        return await self.inner.get_balance(tenant, account_ref)


class UnavailableOnce:
    """The gateway is briefly down: the first call fails before reaching the ledger."""

    def __init__(self, inner: McpLedgerTools) -> None:
        self.inner = inner
        self.calls = 0

    async def record_promise_to_pay(
        self, tenant: str, args: dict[str, Any], **kw: Any
    ) -> LedgerResult:
        self.calls += 1
        if self.calls == 1:
            return LedgerResult("unavailable", detail="ConnectError")
        return await self.inner.record_promise_to_pay(tenant, args, **kw)

    async def get_balance(self, tenant: str, account_ref: str) -> dict[str, Any]:
        return await self.inner.get_balance(tenant, account_ref)


async def test_a_transient_ledger_failure_is_retried_with_back_off(
    sessionmaker, database_url, migrated_database, tenant, stack
) -> None:  # type: ignore[no-untyped-def]
    """T-51 reviews: `unavailable` left the promise `failed` for good (round 1); redelivering the
    reply instead flooded the DLQ through the stale-reply sweep (round 2). The reply completes,
    the promise stays pending, and a backed-off follow-up retries the same key."""
    account, _ = await seeded_account(sessionmaker, stack, tenant, 120_000)
    tools = McpLedgerTools(stack.gateway_url, DevIssuerTokenProvider(stack.issuer_url))
    services = services_with(sessionmaker, database_url, UnavailableOnce(tools))
    graph = build_reply_graph(services, sessionmaker)
    reply = await inbound(sessionmaker, tenant, account, "I can pay 1,200 on Friday")
    key = promise_key(str(tenant), reply.provider_sid)
    result = await graph.ainvoke({"reply": reply.model_dump(mode="json", by_alias=True)})
    assert result["outcome"] == "promise_retry_scheduled"
    async with sessionmaker() as s:
        repos = Repos(s)
        held = await repos.promises.by_key(tenant, key)
        assert held is not None
        assert held.status == "pending"
        retry = await repos.follow_ups.pending_for_promise(tenant, held.id, "promise_write_back")
        message = await repos.messages.get(tenant, uuid.UUID(reply.message_id))
    assert retry is not None
    assert retry.payload["promise_id"] == str(held.id)
    assert message is not None
    assert message.status != "received"  # handled: the stale-reply sweep leaves it alone
    # What the scheduler's retry_write_back task runs when the follow-up is due.
    assert await retry_write_back(services, sessionmaker, str(tenant), str(held.id)) == (
        "promise_recorded"
    )
    assert ledger_rows(migrated_database, tenant) == [(key, 120_000)]


class AlwaysUnavailable:
    async def record_promise_to_pay(
        self, tenant: str, args: dict[str, Any], **_: Any
    ) -> LedgerResult:
        return LedgerResult("unavailable", detail="ConnectError")

    async def get_balance(self, tenant: str, account_ref: str) -> dict[str, Any]:
        return {}


async def test_write_back_gives_up_after_the_last_attempt(
    sessionmaker, database_url, migrated_database, tenant, stack
) -> None:  # type: ignore[no-untyped-def]
    from collections_copilot.agent.nodes.record_promise import MAX_WRITE_BACK_ATTEMPTS

    account, _ = await seeded_account(sessionmaker, stack, tenant, 120_000)
    services = make_services(sessionmaker, database_url)
    services.ledger = AlwaysUnavailable()
    graph = build_reply_graph(services, sessionmaker)
    reply = await inbound(sessionmaker, tenant, account, "I can pay 1,200 on Friday")
    await graph.ainvoke({"reply": reply.model_dump(mode="json", by_alias=True)})
    async with sessionmaker() as s:
        promise = await Repos(s).promises.by_key(
            tenant, promise_key(str(tenant), reply.provider_sid)
        )
    assert promise is not None
    outcomes = [
        await retry_write_back(services, sessionmaker, str(tenant), str(promise.id))
        for _ in range(MAX_WRITE_BACK_ATTEMPTS)
    ]
    assert outcomes[:-1] == ["promise_retry_scheduled"] * (MAX_WRITE_BACK_ATTEMPTS - 1)
    assert outcomes[-1] == "promise_failed"
    assert services.notifier.sent  # a supervisor is told
    assert ledger_rows(migrated_database, tenant) == []

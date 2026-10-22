"""The synthetic ledger MCP server: tool schemas and upstream dedup by idempotency key."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

import psycopg
import pytest
from ledger_mcp.server import build_server
from ledger_mcp.store import LedgerStore
from mcp import Client

pytestmark = pytest.mark.integration


@pytest.fixture
async def ledger(migrated_database: str) -> Any:
    store = LedgerStore(migrated_database)
    await store.open()
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    await store.upsert_account(tenant, "A00001", "Synthetic Co", "USD")
    await store.upsert_invoice(
        tenant, "A00001", "INV-A00001-1", 120_000, "USD", date.today() - timedelta(days=30)
    )
    await store.upsert_invoice(
        tenant, "A00001", "INV-A00001-2", 30_000, "USD", date.today() - timedelta(days=5)
    )
    try:
        yield store, tenant
    finally:
        await store.close()


def meta(tenant: str, key: str | None = None) -> dict[str, Any]:
    m: dict[str, Any] = {
        "mcpgw": {"tenant": tenant, "agent": "cc-worker", "request_id": uuid.uuid4().hex}
    }
    if key:
        m["idempotency_key"] = key
    return m


async def test_tool_schemas_match_contract(ledger: Any) -> None:
    store, _ = ledger
    async with Client(build_server(store)) as client:
        tools = {t.name: t.input_schema for t in (await client.list_tools()).tools}
    assert set(tools) == {
        "get_balance",
        "list_open_invoices",
        "record_promise_to_pay",
        "add_collection_note",
    }
    rp = tools["record_promise_to_pay"]
    assert set(rp["required"]) == {
        "account_ref",
        "amount_cents",
        "currency",
        "promised_date",
        "invoice_numbers",
        "source_message_sid",
    }
    assert rp["properties"]["amount_cents"]["exclusiveMinimum"] == 0
    assert rp["properties"]["invoice_numbers"]["minItems"] == 1
    assert tools["add_collection_note"]["properties"]["body"]["maxLength"] == 1000


async def test_record_promise_unique_request_id(ledger: Any, migrated_database: str) -> None:
    store, tenant = ledger
    args = {
        "account_ref": "A00001",
        "amount_cents": 120_000,
        "currency": "USD",
        "promised_date": (date.today() + timedelta(days=3)).isoformat(),
        "invoice_numbers": ["INV-A00001-1"],
        "source_message_sid": "SM" + "a" * 32,
    }
    key = f"ptp:{tenant}:SM{'a' * 32}"
    async with Client(build_server(store)) as client:
        first = await client.call_tool("record_promise_to_pay", args, meta=meta(tenant, key))
        second = await client.call_tool("record_promise_to_pay", args, meta=meta(tenant, key))
        assert not first.is_error, first.content
        assert first.structured_content["ledger_ref"] == second.structured_content["ledger_ref"]
        other = await client.call_tool("record_promise_to_pay", args, meta=meta(tenant, key + "x"))
        assert other.structured_content["ledger_ref"] != first.structured_content["ledger_ref"]
        no_key = await client.call_tool("record_promise_to_pay", args, meta=meta(tenant))
        assert no_key.is_error
        no_tenant = await client.call_tool("get_balance", {"account_ref": "A00001"})
        assert no_tenant.is_error
        balance = await client.call_tool(
            "get_balance", {"account_ref": "A00001"}, meta=meta(tenant)
        )
        assert balance.structured_content["balance_cents"] == 150_000
        invoices = await client.call_tool(
            "list_open_invoices", {"account_ref": "A00001"}, meta=meta(tenant)
        )
        assert [i["number"] for i in invoices.structured_content["invoices"]] == [
            "INV-A00001-1",
            "INV-A00001-2",
        ]
        bad = await client.call_tool(
            "record_promise_to_pay", {**args, "amount_cents": -5}, meta=meta(tenant, key + "y")
        )
        assert bad.is_error
        note = await client.call_tool(
            "add_collection_note",
            {"account_ref": "A00001", "body": "Promise recorded"},
            meta=meta(tenant, f"note:{tenant}:1"),
        )
        assert not note.is_error
    with psycopg.connect(migrated_database) as conn:
        rows = conn.execute(
            "SELECT count(*) FROM ledger.promises WHERE tenant=%s AND idempotency_key=%s",
            (tenant, key),
        ).fetchone()
    assert rows == (1,)


async def test_other_tenant_sees_nothing(ledger: Any) -> None:
    store, _ = ledger
    async with Client(build_server(store)) as client:
        result = await client.call_tool(
            "get_balance", {"account_ref": "A00001"}, meta=meta("someone-else")
        )
    assert result.is_error


async def test_foreign_invoice_and_changed_replay_rejected(ledger: Any) -> None:
    store, tenant = ledger
    args = {
        "account_ref": "A00001",
        "amount_cents": 30_000,
        "currency": "USD",
        "promised_date": (date.today() + timedelta(days=3)).isoformat(),
        "invoice_numbers": ["INV-A00001-2"],
        "source_message_sid": "SM" + "b" * 32,
    }
    key = f"ptp:{tenant}:SM{'b' * 32}"
    async with Client(build_server(store)) as client:
        foreign = await client.call_tool(
            "record_promise_to_pay",
            {**args, "invoice_numbers": ["INV-OTHER-1"]},
            meta=meta(tenant, key),
        )
        assert foreign.is_error
        first = await client.call_tool("record_promise_to_pay", args, meta=meta(tenant, key))
        assert not first.is_error
        changed = await client.call_tool(
            "record_promise_to_pay", {**args, "amount_cents": 99_900}, meta=meta(tenant, key)
        )
        assert changed.is_error

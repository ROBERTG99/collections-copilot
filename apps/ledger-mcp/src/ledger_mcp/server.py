"""Synthetic ledger exposed as an MCP server (MCPServer, Streamable HTTP on :8343).

The tenant comes only from `_meta.mcpgw.tenant`, which mcp-gateway sets from the agent JWT; calls
without it are rejected. Write tools require `_meta.idempotency_key` and dedup on it upstream too.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date
from typing import Annotated, Any

import uvicorn
from mcp.server.mcpserver import Context, MCPServer
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from ledger_mcp.store import LedgerStore


class ToolInputError(ValueError):
    pass


class Balance(BaseModel):
    account_ref: str
    balance_cents: int
    currency: str
    open_invoices: int


class InvoiceOut(BaseModel):
    number: str
    amount_cents: int
    currency: str
    due_on: date


class OpenInvoices(BaseModel):
    account_ref: str
    invoices: list[InvoiceOut]


class PromiseRecorded(BaseModel):
    ledger_ref: str
    replayed: bool


class NoteAdded(BaseModel):
    note_id: str
    replayed: bool


AccountRef = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")]


def _meta(ctx: Context) -> dict[str, Any]:
    raw: Any = ctx.request_context.meta
    if raw is None:
        return {}
    if hasattr(raw, "model_dump"):
        return dict(raw.model_dump(by_alias=True, exclude_none=True))
    return dict(raw)


def _tenant(ctx: Context) -> str:
    tenant = (_meta(ctx).get("mcpgw") or {}).get("tenant")
    if not tenant:
        raise ToolInputError("calls must come through mcp-gateway (missing _meta.mcpgw.tenant)")
    return str(tenant)


def _key(ctx: Context) -> str:
    key = _meta(ctx).get("idempotency_key")
    if not isinstance(key, str) or not key:
        raise ToolInputError("write tools require _meta.idempotency_key")
    return key


def build_server(store: LedgerStore) -> MCPServer:
    server = MCPServer(
        name="ledger", version="0.1.0", instructions="Synthetic ledger (collections-copilot)."
    )
    read_only = ToolAnnotations(read_only_hint=True)
    write = ToolAnnotations(read_only_hint=False, idempotent_hint=True)

    async def _account(tenant: str, ref: str) -> dict[str, Any]:
        account = await store.account(tenant, ref)
        if account is None:
            raise ToolInputError(f"unknown account {ref}")
        return account

    @server.tool(
        name="get_balance", annotations=read_only, description="Open balance for an account."
    )
    async def get_balance(account_ref: AccountRef, ctx: Context) -> Balance:
        tenant = _tenant(ctx)
        account = await _account(tenant, account_ref)
        invoices = await store.open_invoices(tenant, account_ref)
        return Balance(
            account_ref=account_ref,
            balance_cents=sum(int(i["amount_cents"]) for i in invoices),
            currency=str(account["currency"]),
            open_invoices=len(invoices),
        )

    @server.tool(name="list_open_invoices", annotations=read_only, description="Open invoices.")
    async def list_open_invoices(account_ref: AccountRef, ctx: Context) -> OpenInvoices:
        tenant = _tenant(ctx)
        await _account(tenant, account_ref)
        rows = await store.open_invoices(tenant, account_ref)
        return OpenInvoices(account_ref=account_ref, invoices=[InvoiceOut(**r) for r in rows])

    @server.tool(
        name="record_promise_to_pay",
        annotations=write,
        description="Record a promise to pay (write).",
    )
    async def record_promise_to_pay(
        account_ref: AccountRef,
        amount_cents: Annotated[int, Field(gt=0)],
        currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")],
        promised_date: date,
        invoice_numbers: Annotated[list[str], Field(min_length=1, max_length=50)],
        source_message_sid: Annotated[str, Field(min_length=2, max_length=64)],
        ctx: Context,
    ) -> PromiseRecorded:
        tenant = _tenant(ctx)
        key = _key(ctx)
        await _account(tenant, account_ref)
        ledger_ref, replayed = await store.record_promise(
            tenant,
            key,
            {
                "account_ref": account_ref,
                "amount_cents": amount_cents,
                "currency": currency,
                "promised_date": promised_date,
                "invoice_numbers": invoice_numbers,
                "source_message_sid": source_message_sid,
            },
        )
        return PromiseRecorded(ledger_ref=ledger_ref, replayed=replayed)

    @server.tool(
        name="add_collection_note", annotations=write, description="Add a collection note (write)."
    )
    async def add_collection_note(
        account_ref: AccountRef,
        body: Annotated[str, Field(min_length=1, max_length=1000)],
        ctx: Context,
    ) -> NoteAdded:
        tenant = _tenant(ctx)
        key = _key(ctx)
        await _account(tenant, account_ref)
        note_id, replayed = await store.add_note(tenant, key, account_ref, body)
        return NoteAdded(note_id=note_id, replayed=replayed)

    @server.custom_route("/healthz", methods=["GET"])  # type: ignore[untyped-decorator]
    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return server


async def serve() -> None:
    store = LedgerStore(
        os.environ.get("LEDGER_DATABASE_URL", "postgresql://cc:cc@localhost:8320/cc")
    )
    await store.open()
    server = build_server(store)
    host = os.environ.get("LEDGER_HOST", "0.0.0.0")  # noqa: S104 - container port
    app = server.streamable_http_app(json_response=True, stateless_http=True, host=host)
    try:
        await uvicorn.Server(
            uvicorn.Config(
                app, host=host, port=int(os.environ.get("LEDGER_PORT", "8343")), log_level="warning"
            )
        ).serve()
    finally:
        await store.close()


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()

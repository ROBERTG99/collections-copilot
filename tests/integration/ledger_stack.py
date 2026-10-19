"""In-process ledger-mcp + mcp-gateway (fallback) on free ports, against the test database."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uvicorn
from ledger_mcp.server import build_server
from ledger_mcp.store import LedgerStore
from mcpgw_lite.config import Config
from mcpgw_lite.server import running

ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class LedgerStack:
    gateway_url: str
    admin_url: str
    issuer_url: str
    store: LedgerStore
    gateway: Any


async def _serve(app: Any, port: int) -> tuple[uvicorn.Server, asyncio.Task[None]]:
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.02)
    return server, task


@asynccontextmanager
async def ledger_stack(libpq_url: str, webhook_url: str = "") -> AsyncIterator[LedgerStack]:
    store = LedgerStore(libpq_url)
    await store.open()
    ledger_port, gw_port, admin_port, issuer_port = (
        free_port(),
        free_port(),
        free_port(),
        free_port(),
    )
    ledger_app = build_server(store).streamable_http_app(
        json_response=True, stateless_http=True, host="127.0.0.1"
    )
    servers = [await _serve(ledger_app, ledger_port)]
    cfg = Config(
        database_url=libpq_url,
        upstreams={"ledger": f"http://127.0.0.1:{ledger_port}/mcp"},
        jwks_urls=(),
        policy_file=str(ROOT / "deploy/mcpgw/policy/collections.yaml"),
        webhook_url=webhook_url,
        webhook_secret="test-secret",
        approval_ttl_s=900,
        dev_issuer=True,
        host="127.0.0.1",
        port=gw_port,
        admin_port=admin_port,
        issuer_port=issuer_port,
    )
    async with running(cfg) as (gw, mcp_app, admin_app, issuer_app):
        servers.append(await _serve(mcp_app, gw_port))
        servers.append(await _serve(admin_app, admin_port))
        servers.append(await _serve(issuer_app, issuer_port))
        try:
            yield LedgerStack(
                f"http://127.0.0.1:{gw_port}/mcp",
                f"http://127.0.0.1:{admin_port}",
                f"http://127.0.0.1:{issuer_port}",
                store,
                gw,
            )
        finally:
            for server, task in reversed(servers):
                server.should_exit = True
                await task
            await store.close()

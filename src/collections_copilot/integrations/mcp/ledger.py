"""LedgerTools: the ledger MCP tools through mcp-gateway (Streamable HTTP + agent JWT).

Write calls carry `_meta.idempotency_key`; retries after a supervisor approval add
`_meta.approval_id` with the same key. Results come from `structuredContent`, are treated as
untrusted data, and are never placed in prompts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

from collections_copilot.integrations.mcp.tokens import AgentTokenProvider
from collections_copilot.observability import tracing

E_IDEMPOTENCY_CONFLICT = -32040
E_APPROVAL_INVALID = -32045
E_UPSTREAM_UNAVAILABLE = -32046
E_IDEMPOTENCY_IN_PROGRESS = -32047  # the first call with this key has not finished yet
LedgerStatus = Literal[
    "recorded",
    "approval_required",
    "idempotency_conflict",
    "approval_invalid",
    "unavailable",
    "rejected",
    "error",
]


@dataclass(frozen=True)
class LedgerResult:
    status: LedgerStatus
    ledger_ref: str | None = None
    approval_id: str | None = None
    reasons: list[str] = field(default_factory=list)
    detail: str | None = None
    replayed: bool = False


class LedgerTools(Protocol):
    async def record_promise_to_pay(
        self,
        tenant: str,
        args: dict[str, Any],
        *,
        idempotency_key: str,
        approval_id: str | None = None,
    ) -> LedgerResult: ...

    async def get_balance(self, tenant: str, account_ref: str) -> dict[str, Any]: ...


def _find_mcp_error(exc: BaseException) -> MCPError | None:
    if isinstance(exc, MCPError):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            found = _find_mcp_error(inner)
            if found is not None:
                return found
    cause = exc.__cause__ or exc.__context__
    return _find_mcp_error(cause) if cause is not None and cause is not exc else None


class McpLedgerTools:
    def __init__(
        self, gateway_url: str, tokens: AgentTokenProvider, timeout_s: float = 20.0
    ) -> None:
        self.url = gateway_url
        self.tokens = tokens
        self.timeout_s = timeout_s

    async def _call(
        self, tenant: str, scopes: list[str], tool: str, args: dict[str, Any], meta: dict[str, Any]
    ) -> Any:
        token = await self.tokens.token(tenant, scopes)
        http = httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {token}"}, timeout=self.timeout_s
        )
        with tracing.span(
            f"tool {tool}",
            {"cc.tool": tool, "cc.tenant_id": tenant, "rpc.system": "mcp"},
            kind=tracing.SpanKind.CLIENT,
        ):
            async with http, Client(streamable_http_client(self.url, http_client=http)) as client:
                return await client.call_tool(tool, args, meta=cast(Any, meta) or None)

    async def get_balance(self, tenant: str, account_ref: str) -> dict[str, Any]:
        result = await self._call(
            tenant, ["ledger:read"], "ledger.get_balance", {"account_ref": account_ref}, {}
        )
        data = result.structured_content or {}
        return dict(data) if isinstance(data, dict) else {}

    async def record_promise_to_pay(
        self,
        tenant: str,
        args: dict[str, Any],
        *,
        idempotency_key: str,
        approval_id: str | None = None,
    ) -> LedgerResult:
        meta: dict[str, Any] = {"idempotency_key": idempotency_key}
        if approval_id:
            meta["approval_id"] = approval_id
        try:
            result = await self._call(
                tenant, ["ledger:write"], "ledger.record_promise_to_pay", args, meta
            )
        except Exception as exc:  # MCP errors arrive wrapped in anyio exception groups
            mcp_error = _find_mcp_error(exc)
            if mcp_error is None:  # gateway down, network, auth
                return LedgerResult("unavailable", detail=type(exc).__name__)
            if mcp_error.code == E_IDEMPOTENCY_CONFLICT:
                return LedgerResult("idempotency_conflict", detail=mcp_error.message)
            if mcp_error.code == E_APPROVAL_INVALID:
                data = mcp_error.data if isinstance(mcp_error.data, dict) else {}
                return LedgerResult(
                    "approval_invalid", detail=str(data.get("reason") or mcp_error.message)
                )
            if mcp_error.code in (E_UPSTREAM_UNAVAILABLE, E_IDEMPOTENCY_IN_PROGRESS):
                return LedgerResult("unavailable", detail=mcp_error.message)
            return LedgerResult("error", detail=f"{mcp_error.code}: {mcp_error.message}")
        data = result.structured_content if isinstance(result.structured_content, dict) else {}
        if result.is_error:
            if data.get("status") == "approval_required":
                approval_id = data.get("approval_id")
                if not isinstance(approval_id, str) or not approval_id:
                    return LedgerResult("error", detail="approval_required without approval_id")
                return LedgerResult(
                    "approval_required",
                    approval_id=approval_id,
                    reasons=[str(r) for r in data.get("reasons", [])],
                )
            return LedgerResult(
                "rejected", detail=" ".join(getattr(c, "text", "") for c in result.content)[:300]
            )
        meta_out = getattr(result, "meta", None) or {}
        replay = bool(
            ((meta_out.get("mcpgw") or {}) if isinstance(meta_out, dict) else {}).get(
                "idempotent_replay"
            )
        )
        return LedgerResult("recorded", ledger_ref=str(data.get("ledger_ref")), replayed=replay)

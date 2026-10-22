"""mcp-gateway admin API client: supervisor decisions on held tool calls (FR-9).

The API acts as `cc-api` with scope `mcpgw:approve`; the supervisor's username travels in the
note and in our own audit log.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx

from collections_copilot.integrations.mcp.tokens import AgentTokenProvider

Decision = Literal["approve", "reject"]


class GatewayAdminError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


class GatewayAdmin(Protocol):
    async def decide(
        self, tenant: str, approval_id: str, decision: Decision, note: str
    ) -> dict[str, Any]: ...

    async def get(self, tenant: str, approval_id: str) -> dict[str, Any]: ...


@dataclass
class HttpGatewayAdmin:
    admin_url: str
    tokens: AgentTokenProvider
    timeout_s: float = 10.0

    async def _request(self, tenant: str, method: str, path: str, **kw: Any) -> dict[str, Any]:
        token = await self.tokens.token(tenant, ["mcpgw:approve"])
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.request(
                    method,
                    f"{self.admin_url.rstrip('/')}{path}",
                    headers={"Authorization": f"Bearer {token}"},
                    **kw,
                )
        except httpx.HTTPError as exc:
            raise GatewayAdminError(503, type(exc).__name__) from exc
        if resp.status_code >= 400:
            raise GatewayAdminError(resp.status_code, resp.text[:200])
        data: dict[str, Any] = resp.json()
        return data

    async def decide(
        self, tenant: str, approval_id: str, decision: Decision, note: str
    ) -> dict[str, Any]:
        _check_id(approval_id)
        body = {"note": note} if decision == "approve" else {"reason": note}
        return await self._request(
            tenant, "POST", f"/admin/approvals/{approval_id}:{decision}", json=body
        )

    async def get(self, tenant: str, approval_id: str) -> dict[str, Any]:
        _check_id(approval_id)
        return await self._request(tenant, "GET", f"/admin/approvals/{approval_id}")


APPROVAL_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _check_id(approval_id: str) -> None:
    """Approval ids go into admin URL paths: plain identifiers only."""
    if not APPROVAL_ID.fullmatch(approval_id):
        raise ValueError("approval_id must be 1-64 letters, digits, '-' or '_'")

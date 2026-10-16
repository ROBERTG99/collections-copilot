"""mcp-gateway contract subset: MCP proxy (8344), admin API (8348) and dev issuer (8349).

Contract (mcp-gateway v0.1.0 §4.4): tool names ``<server>.<tool>``; RS256 agent JWT; write tools
require ``_meta.idempotency_key``; replay on same args, ``-32040`` on different args; policy
``require_approval`` returns ``isError`` with ``structuredContent.status = approval_required`` and
posts a signed webhook; retries carry ``_meta.approval_id`` and the same key.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

import httpx
import mcp_types as types
import uvicorn
from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from mcp import Client
from mcp.server import Server
from mcp.shared.exceptions import MCPError

from mcpgw_lite.auth import AuthError, Claims, DevIssuer, Verifier
from mcpgw_lite.config import Config
from mcpgw_lite.policy import Policy
from mcpgw_lite.store import Store, args_sha256

log = logging.getLogger("mcpgw")

E_IDEMPOTENCY_CONFLICT = -32040
E_IDEMPOTENCY_KEY_REQUIRED = -32041
E_POLICY_DENIED = -32044
E_APPROVAL_INVALID = -32045
E_UPSTREAM_UNAVAILABLE = -32046
E_IDEMPOTENCY_IN_PROGRESS = -32047
E_UNKNOWN_TOOL = -32602
KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
UNTRUSTED_PREFIX = "<<untrusted tool output>>"

_claims: ContextVar[Claims | None] = ContextVar("mcpgw_claims", default=None)

Scope = MutableMapping[str, Any]
ASGIApp = Callable[
    [Scope, Callable[[], Awaitable[Any]], Callable[[Any], Awaitable[None]]], Awaitable[None]
]


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return EMAIL_RE.sub("[email]", PHONE_RE.sub("[phone]", value))
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, uuid.UUID):
        return str(o)
    raise TypeError(type(o).__name__)


class Gateway:
    def __init__(self, cfg: Config, store: Store, policy: Policy, verifier: Verifier) -> None:
        self.cfg = cfg
        self.store = store
        self.policy = policy
        self.verifier = verifier
        self._tool_cache: dict[str, types.Tool] = {}
        self._bg: set[asyncio.Task[None]] = set()

    # upstream ---------------------------------------------------------------
    async def _upstream_tools(self) -> list[types.Tool]:
        tools: list[types.Tool] = []
        for server, url in self.cfg.upstreams.items():
            async with Client(url) as client:
                listed = await client.list_tools()
            for t in listed.tools:
                name = f"{server}.{t.name}"
                tool = t.model_copy(update={"name": name})
                self._tool_cache[name] = tool
                tools.append(tool)
        return tools

    async def list_tools(self, claims: Claims) -> list[types.Tool]:
        allowed = self.policy.agents.get(claims.sub, set())
        tools = await self._upstream_tools()
        return [
            t
            for t in tools
            if t.name in allowed
            and self.policy.tools.get(t.name) is not None
            and self.policy.tools[t.name].scope in claims.scopes
        ]

    async def _call_upstream(
        self, tool: str, args: dict[str, Any], meta: dict[str, Any]
    ) -> dict[str, Any]:
        server, _, name = tool.partition(".")
        url = self.cfg.upstreams.get(server)
        if url is None:
            raise MCPError(E_UNKNOWN_TOOL, f"unknown tool {tool}")
        try:
            async with Client(url, read_timeout_seconds=20) as client:
                result = await client.call_tool(name, args, meta=meta)
        except MCPError:
            raise
        except Exception as exc:
            raise MCPError(
                E_UPSTREAM_UNAVAILABLE, "upstream_unavailable", {"error": str(exc)}
            ) from exc
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    # helpers ----------------------------------------------------------------
    def _untrusted(
        self, payload: dict[str, Any], replay: bool, audit_seq: int
    ) -> types.CallToolResult:
        structured = payload.get("structuredContent")
        if isinstance(structured, dict):
            structured = {**structured, "untrusted": True}
        elif structured is None:
            structured = {"untrusted": True}
        content = [
            types.TextContent(type="text", text=f"{UNTRUSTED_PREFIX}\n{c.get('text', '')}")
            for c in payload.get("content", [])
            if c.get("type") == "text"
        ]
        return types.CallToolResult(
            content=content,
            structured_content=structured,
            is_error=bool(payload.get("isError", False)),
            _meta={
                "mcpgw": {
                    "idempotent_replay": replay,
                    "audit_seq": audit_seq,
                    "injection": {"score": 0.0, "signals": []},
                }
            },
        )

    async def _audit(self, claims: Claims, tool: str, **kw: Any) -> int:
        server, _, name = tool.partition(".")
        return await self.store.audit(
            {"tenant": claims.tenant, "agent": claims.sub, "server": server, "tool": name, **kw}
        )

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _send_webhook(self, approval: dict[str, Any]) -> None:
        if not self.cfg.webhook_url:
            return
        body = json.dumps(
            {
                "approval_id": str(approval["id"]),
                "tenant": approval["tenant"],
                "agent": approval["agent"],
                "tool": approval["tool"],
                "args_redacted": approval["args_redacted"],
                "reasons": approval["reasons"],
                "expires_at": approval["expires_at"],
            },
            default=_json_default,
        ).encode()
        sig = hmac.new(self.cfg.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        headers = {"Content-Type": "application/json", "X-MCPGW-Signature": f"sha256={sig}"}
        delay = 0.5
        for _ in range(6):
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.post(self.cfg.webhook_url, content=body, headers=headers)
                if resp.status_code < 500:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(delay)
            delay *= 2
        log.warning("approval webhook delivery failed", extra={"approval_id": str(approval["id"])})

    # tools/call -------------------------------------------------------------
    async def call_tool(
        self, claims: Claims, tool: str, args: dict[str, Any], meta: dict[str, Any]
    ) -> types.CallToolResult:
        t0 = time.perf_counter()
        tp = self.policy.tools.get(tool)
        if tp is None or tool.partition(".")[0] not in self.cfg.upstreams:
            raise MCPError(E_UNKNOWN_TOOL, f"unknown tool {tool}")
        decision = self.policy.evaluate(claims.sub, claims.scopes, tool, args)
        sha = args_sha256(args)
        if decision.effect == "deny":
            await self._audit(claims, tool, args_sha256=sha, decision="deny", status="denied")
            raise MCPError(E_POLICY_DENIED, "policy_denied", {"reasons": decision.reasons})
        request_id = uuid.uuid4().hex
        if not tp.write:
            payload = await self._call_upstream(
                tool,
                args,
                {"mcpgw": {"tenant": claims.tenant, "agent": claims.sub, "request_id": request_id}},
            )
            seq = await self._audit(
                claims,
                tool,
                args_sha256=sha,
                decision="allow",
                status="ok",
                latency_ms=(time.perf_counter() - t0) * 1000,
            )
            return self._untrusted(payload, False, seq)

        key = meta.get("idempotency_key")
        if not isinstance(key, str) or not KEY_RE.match(key):
            raise MCPError(E_IDEMPOTENCY_KEY_REQUIRED, "idempotency_key_required")
        existing = await self.store.idem_get(claims.tenant, key)
        if existing is not None:
            if existing["args_sha256"] != sha:
                await self._audit(
                    claims,
                    tool,
                    args_sha256=sha,
                    decision="deny",
                    idempotency_key=key,
                    status="idempotency_conflict",
                )
                raise MCPError(E_IDEMPOTENCY_CONFLICT, "idempotency_conflict")
            if existing["status"] == "completed":
                seq = await self._audit(
                    claims,
                    tool,
                    args_sha256=sha,
                    decision="replay",
                    idempotency_key=key,
                    status="ok",
                )
                return self._untrusted(existing["result"], True, seq)
            raise MCPError(E_IDEMPOTENCY_IN_PROGRESS, "idempotency_in_progress")

        approval_id = meta.get("approval_id")
        if decision.effect == "require_approval":
            if approval_id:
                appr = await self.store.approval_get(str(approval_id))
                reason = None
                if appr is None or appr["tenant"] != claims.tenant:
                    reason = "approval_not_found"
                elif appr["args_sha256"] != sha or appr["idempotency_key"] != key:
                    reason = "approval_mismatch"
                elif appr["status"] == "rejected":
                    reason = "approval_rejected"
                elif appr["status"] == "consumed":
                    reason = "approval_consumed"
                elif appr["expires_at"] <= datetime.now(UTC):
                    reason = "approval_expired"
                elif appr["status"] != "approved":
                    reason = "approval_pending"
                if reason:
                    await self._audit(
                        claims,
                        tool,
                        args_sha256=sha,
                        decision="require_approval",
                        idempotency_key=key,
                        status=reason,
                    )
                    raise MCPError(E_APPROVAL_INVALID, "approval_invalid", {"reason": reason})
            else:
                appr = await self.store.approval_pending_for_key(claims.tenant, key)
                if appr is None or appr["args_sha256"] != sha:
                    appr = await self.store.approval_create(
                        claims.tenant,
                        claims.sub,
                        tool,
                        key,
                        sha,
                        redact(args),
                        decision.reasons,
                        self.cfg.approval_ttl_s,
                    )
                    self._spawn(self._send_webhook(appr))
                seq = await self._audit(
                    claims,
                    tool,
                    args_sha256=sha,
                    decision="require_approval",
                    approval_id=appr["id"],
                    idempotency_key=key,
                    status="held",
                )
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text="approval_required")],
                    structured_content={
                        "status": "approval_required",
                        "approval_id": str(appr["id"]),
                        "reasons": list(appr["reasons"]),
                    },
                    is_error=True,
                    _meta={"mcpgw": {"audit_seq": seq, "idempotent_replay": False}},
                )

        if not await self.store.idem_begin(claims.tenant, key, tool, sha):
            raise MCPError(E_IDEMPOTENCY_IN_PROGRESS, "idempotency_in_progress")
        try:
            payload = await self._call_upstream(
                tool,
                args,
                {
                    "idempotency_key": key,
                    "mcpgw": {
                        "tenant": claims.tenant,
                        "agent": claims.sub,
                        "request_id": request_id,
                    },
                },
            )
        except MCPError:
            await self.store.idem_abort(claims.tenant, key)
            await self._audit(
                claims, tool, args_sha256=sha, decision="allow", idempotency_key=key, status="error"
            )
            raise
        if payload.get("isError"):
            await self.store.idem_abort(claims.tenant, key)
        else:
            await self.store.idem_complete(claims.tenant, key, payload)
            if approval_id:
                await self.store.approval_consume(str(approval_id))
        seq = await self._audit(
            claims,
            tool,
            args_sha256=sha,
            decision="allow",
            approval_id=approval_id,
            idempotency_key=key,
            status="error" if payload.get("isError") else "ok",
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
        return self._untrusted(payload, False, seq)


def _meta_dict(meta: Any) -> dict[str, Any]:
    if meta is None:
        return {}
    if hasattr(meta, "model_dump"):
        return dict(meta.model_dump(by_alias=True, exclude_none=True))
    return dict(meta)


def build_mcp_server(gw: Gateway) -> Server[Any]:
    def _ctx_claims(ctx: Any) -> Claims:
        claims = _claims.get()
        if claims is None:
            request = getattr(ctx, "request", None)
            scope = getattr(request, "scope", None) or {}
            claims = (scope.get("state") or {}).get("mcpgw_claims")
        if claims is None:
            raise MCPError(-32001, "unauthenticated")
        return claims

    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        claims = _ctx_claims(ctx)
        return types.ListToolsResult(tools=await gw.list_tools(claims))

    async def on_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        claims = _ctx_claims(ctx)
        meta = _meta_dict(getattr(ctx, "meta", None)) or _meta_dict(getattr(params, "meta", None))
        return await gw.call_tool(claims, params.name, params.arguments or {}, meta)

    return Server("mcp-gateway", on_list_tools=on_list_tools, on_call_tool=on_call_tool)


class AuthMiddleware:
    """Bearer JWT on /mcp; serves /healthz and /readyz itself."""

    def __init__(self, app: ASGIApp, gw: Gateway) -> None:
        self.app = app
        self.gw = gw

    async def _send_json(
        self,
        send: Any,
        status: int,
        body: dict[str, Any],
        extra: list[tuple[bytes, bytes]] | None = None,
    ) -> None:
        raw = json.dumps(body).encode()
        headers = [(b"content-type", b"application/json"), *(extra or [])]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": raw})

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path == "/healthz":
            await self._send_json(send, 200, {"status": "ok"})
            return
        if path == "/readyz":
            ok = await self.gw.store.ping()
            await self._send_json(
                send, 200 if ok else 503, {"status": "ok" if ok else "unavailable"}
            )
            return
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode()
        if not auth.lower().startswith("bearer "):
            await self._send_json(
                send,
                401,
                {"error": "invalid_token"},
                [(b"www-authenticate", b'Bearer error="invalid_token"')],
            )
            return
        try:
            claims = await self.gw.verifier.verify(auth[7:].strip())
        except AuthError as exc:
            await self._send_json(
                send,
                401,
                {"error": "invalid_token", "detail": str(exc)},
                [(b"www-authenticate", b'Bearer error="invalid_token"')],
            )
            return
        scope.setdefault("state", {})["mcpgw_claims"] = claims
        token = _claims.set(claims)
        try:
            await self.app(scope, receive, send)
        finally:
            _claims.reset(token)


def build_admin_app(gw: Gateway) -> FastAPI:
    app = FastAPI(title="mcp-gateway admin (subset)")

    async def _admin(authorization: str | None, scope: str) -> Claims:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "invalid_token")
        try:
            claims = await gw.verifier.verify(authorization[7:].strip())
        except AuthError as exc:
            raise HTTPException(401, "invalid_token") from exc
        if scope not in claims.scopes and "mcpgw:admin" not in claims.scopes:
            raise HTTPException(403, "forbidden")
        return claims

    def _out(row: dict[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(row, default=_json_default))

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/admin/approvals")
    async def list_approvals(
        status: str | None = None, authorization: str | None = Header(None)
    ) -> list[dict[str, Any]]:
        claims = await _admin(authorization, "mcpgw:approve")
        return [_out(r) for r in await gw.store.approval_list(claims.tenant, status)]

    @app.get("/admin/approvals/{approval_id}")
    async def get_approval(
        approval_id: str, authorization: str | None = Header(None)
    ) -> dict[str, Any]:
        claims = await _admin(authorization, "mcpgw:approve")
        row = await gw.store.approval_get(approval_id)
        if row is None or row["tenant"] != claims.tenant:
            raise HTTPException(404, "not_found")
        return _out(row)

    async def _decide(
        approval_id: str, status: str, authorization: str | None, note: str | None
    ) -> JSONResponse:
        claims = await _admin(authorization, "mcpgw:approve")
        row = await gw.store.approval_get(approval_id)
        if row is None or row["tenant"] != claims.tenant:
            raise HTTPException(404, "not_found")
        if row["expires_at"] <= datetime.now(UTC) and row["status"] == "pending":
            raise HTTPException(410, "expired")
        updated = await gw.store.approval_decide(approval_id, status, claims.sub, note)
        if updated is None:
            raise HTTPException(409, "not_pending")
        await gw.store.audit(
            {
                "tenant": claims.tenant,
                "agent": claims.sub,
                "server": "mcpgw",
                "tool": f"approval.{'approve' if status == 'approved' else 'reject'}",
                "decision": status,
                "approval_id": updated["id"],
                "status": "ok",
            }
        )
        return JSONResponse(
            {
                "id": str(updated["id"]),
                "status": updated["status"],
                "decided_by": updated["decided_by"],
                "decided_at": updated["decided_at"].isoformat(),
            }
        )

    @app.post("/admin/approvals/{approval_id}:approve")
    async def approve(
        approval_id: str,
        authorization: str | None = Header(None),
        body: dict[str, Any] | None = Body(None),
    ) -> JSONResponse:
        return await _decide(approval_id, "approved", authorization, (body or {}).get("note"))

    @app.post("/admin/approvals/{approval_id}:reject")
    async def reject(
        approval_id: str,
        authorization: str | None = Header(None),
        body: dict[str, Any] | None = Body(None),
    ) -> JSONResponse:
        reason = (body or {}).get("reason")
        if not reason:
            raise HTTPException(422, "reason required")
        return await _decide(approval_id, "rejected", authorization, reason)

    return app


def build_issuer_app(issuer: DevIssuer) -> FastAPI:
    app = FastAPI(title="mcp-gateway dev issuer")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/.well-known/jwks.json")
    async def jwks() -> dict[str, Any]:
        return issuer.jwks()

    @app.post("/token")
    async def token(request: Request) -> dict[str, Any]:
        body = await request.json()
        ttl = min(int(body.get("ttl_s", 300)), 3600)
        tok = issuer.issue(str(body["sub"]), str(body["tenant"]), list(body.get("scopes", [])), ttl)
        return {"access_token": tok, "token_type": "Bearer", "expires_in": ttl}

    return app


@contextlib.asynccontextmanager
async def running(cfg: Config) -> AsyncIterator[tuple[Gateway, Any, FastAPI, FastAPI | None]]:
    issuer = DevIssuer(cfg.issuer) if cfg.dev_issuer else None
    store = Store(cfg.database_url)
    await store.open()
    gw = Gateway(cfg, store, Policy.load(cfg.policy_file), Verifier(cfg.jwks_urls, issuer))
    server = build_mcp_server(gw)
    mcp_app = AuthMiddleware(
        server.streamable_http_app(json_response=True, stateless_http=True, host=cfg.host), gw
    )
    try:
        yield gw, mcp_app, build_admin_app(gw), build_issuer_app(issuer) if issuer else None
    finally:
        await store.close()


async def serve(cfg: Config | None = None) -> None:
    cfg = cfg or Config()
    async with running(cfg) as (_, mcp_app, admin_app, issuer_app):
        servers = [
            uvicorn.Server(
                uvicorn.Config(mcp_app, host=cfg.host, port=cfg.port, log_level="warning")
            ),
            uvicorn.Server(
                uvicorn.Config(admin_app, host=cfg.host, port=cfg.admin_port, log_level="warning")
            ),
        ]
        if issuer_app is not None:
            servers.append(
                uvicorn.Server(
                    uvicorn.Config(
                        issuer_app, host=cfg.host, port=cfg.issuer_port, log_level="warning"
                    )
                )
            )
        await asyncio.gather(*(s.serve() for s in servers))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='{"level":"%(levelname)s","msg":"%(message)s"}')
    asyncio.run(serve())


if __name__ == "__main__":
    main()

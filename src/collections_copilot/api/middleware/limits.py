"""Request limits (NFR-9) as pure ASGI middleware, outermost first:

- BodyLimitMiddleware: JSON and form bodies ≤ `CC_MAX_JSON_BYTES` (1 MB) → 413. Files never pass
  through the API (CSV uploads go to S3 via presigned URLs, ≤ 10 MB). NUL characters in the
  query string or body → 422 (Postgres text and JSONB cannot store them; found by Schemathesis).
- RateLimitMiddleware: `CC_RATE_LIMIT_PER_MINUTE` (120) requests per user and minute in Redis
  (per client IP before sign-in) → 429 with Retry-After. Webhooks and health checks are exempt.
  If Redis is down the limit is skipped (logged): availability over throttling.
- IdempotencyMiddleware: a mutating `/api/v1` request with `Idempotency-Key` runs once per user
  and key for 24 h. A retry gets the stored response (`Idempotent-Replayed: true`); the same key
  with a different body is 422; a retry while the first is running is 409. 5xx are not stored.

Every rejection is an RFC 9457 problem with `correlation_id`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import time
from typing import Any

from redis.exceptions import RedisError
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from collections_copilot.api.errors import problem

log = logging.getLogger(__name__)
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
EXEMPT_PREFIXES = ("/healthz", "/readyz", "/api/v1/webhooks/", "/docs", "/openapi.json")
# A JSON-escaped NUL (\u0000 not preceded by an odd number of backslashes), a raw NUL, or %00.
NUL_JSON = re.compile(rb"(?<!\\)(?:\\\\)*\\u0000")
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
IDEMPOTENCY_TTL_S = 24 * 3600
MAX_STORED_BODY = 1024 * 1024


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return str(value.decode("latin-1"))
    return None


def _who(scope: Scope) -> str:
    """The caller's identity for limits: a hash of the bearer token, else the client IP."""
    auth = _header(scope, b"authorization") or ""
    if auth.lower().startswith("bearer ") and len(auth) > 7:
        return "u:" + hashlib.sha256(auth[7:].strip().encode()).hexdigest()[:32]
    client = scope.get("client") or ("unknown", 0)
    return f"ip:{client[0]}"


async def _reply(
    scope: Scope,
    receive: Receive,
    send: Send,
    status: int,
    detail: str,
    code: str,
    headers: dict[str, str] | None = None,
) -> None:
    response = problem(Request(scope, receive), status, detail, code, headers=headers)
    await response(scope, receive, send)


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and (
            _has_nul(scope.get("query_string", b"")) or _has_nul(scope.get("raw_path") or b"")
        ):
            await _reply(
                scope, receive, send, 422, "NUL characters are not allowed", "nul_character"
            )
            return
        if scope["type"] != "http" or scope["method"] not in MUTATING:
            await self.app(scope, receive, send)
            return
        length = _header(scope, b"content-length")
        if length and length.isdigit() and int(length) > self.max_bytes:
            await _reply(
                scope,
                receive,
                send,
                413,
                f"request bodies are limited to {self.max_bytes} bytes",
                "too_large",
            )
            return
        # Chunked bodies without a length are counted as they arrive.
        chunks: list[bytes] = []
        size = 0
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > self.max_bytes:
                await _reply(
                    scope,
                    receive,
                    send,
                    413,
                    f"request bodies are limited to {self.max_bytes} bytes",
                    "too_large",
                )
                return
            chunks.append(body)
            more = message.get("more_body", False)
        payload = b"".join(chunks)
        if _has_nul(payload):
            await _reply(
                scope, receive, send, 422, "NUL characters are not allowed", "nul_character"
            )
            return
        await self.app(scope, _replay(payload), send)


def _has_nul(raw: bytes) -> bool:
    return b"\x00" in raw or b"%00" in raw or NUL_JSON.search(raw) is not None


def _replay(body: bytes) -> Receive:
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return receive


async def _read_body(receive: Receive) -> bytes:
    chunks = []
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or path.startswith(EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return
        state = scope["app"].state
        limit = int(state.settings.rate_limit_per_minute)
        now = time.time()
        window = int(now // 60)
        key = f"cc:rl:{_who(scope)}:{window}"
        try:
            pipe = state.redis.pipeline(transaction=True)
            pipe.incr(key)
            pipe.expire(key, 70)
            count = int((await pipe.execute())[0])
        except RedisError:
            log.warning("rate limiter unavailable; request allowed")
            await self.app(scope, receive, send)
            return
        remaining = max(0, limit - count)
        if count > limit:
            retry = str(max(1, int((window + 1) * 60 - now)))
            await _reply(
                scope,
                receive,
                send,
                429,
                f"limit is {limit} requests per minute",
                "rate_limited",
                {
                    "Retry-After": retry,
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                },
            )
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-ratelimit-limit", str(limit).encode()))
                headers.append((b"x-ratelimit-remaining", str(remaining).encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class IdempotencyMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        key = _header(scope, b"idempotency-key")
        if (
            scope["type"] != "http"
            or key is None
            or scope["method"] not in MUTATING
            or not path.startswith("/api/v1/")
            or path.startswith("/api/v1/webhooks/")
        ):
            await self.app(scope, receive, send)
            return
        if not IDEMPOTENCY_KEY.match(key):
            await _reply(
                scope, receive, send, 422, "invalid Idempotency-Key", "invalid_idempotency_key"
            )
            return
        redis = scope["app"].state.redis
        body = await _read_body(receive)
        fingerprint = hashlib.sha256(body).hexdigest()
        store_key = f"cc:idem:{_who(scope)}:{scope['method']}:{path}:{key}"
        try:
            claimed = await redis.set(
                store_key,
                json.dumps({"state": "pending", "fp": fingerprint}),
                nx=True,
                ex=IDEMPOTENCY_TTL_S,
            )
            existing = None if claimed else await redis.get(store_key)
        except RedisError:
            log.warning("idempotency store unavailable; request runs without it")
            await self.app(scope, _replay(body), send)
            return
        if existing is not None:
            await self._existing(scope, receive, send, json.loads(existing), fingerprint)
            return

        captured: dict[str, Any] = {"status": 500, "headers": [], "body": b""}

        async def capture(message: Message) -> None:
            if message["type"] == "http.response.start":
                captured["status"] = message["status"]
                captured["headers"] = message.get("headers", [])
            elif message["type"] == "http.response.body":
                captured["body"] += message.get("body", b"")
            await send(message)

        try:
            await self.app(scope, _replay(body), capture)
        finally:
            await self._store(redis, store_key, fingerprint, captured)

    async def _existing(
        self, scope: Scope, receive: Receive, send: Send, stored: dict[str, Any], fingerprint: str
    ) -> None:
        if stored.get("fp") != fingerprint:
            await _reply(
                scope,
                receive,
                send,
                422,
                "this Idempotency-Key was used with a different body",
                "idempotency_key_reused",
            )
            return
        if stored.get("state") != "done":
            await _reply(
                scope,
                receive,
                send,
                409,
                "a request with this Idempotency-Key is in progress",
                "idempotency_in_progress",
                {"Retry-After": "1"},
            )
            return
        headers = [(k.encode(), v.encode()) for k, v in stored["headers"]]
        headers.append((b"idempotent-replayed", b"true"))
        await send({"type": "http.response.start", "status": stored["status"], "headers": headers})
        await send({"type": "http.response.body", "body": base64.b64decode(stored["body"])})

    async def _store(
        self, redis: Any, store_key: str, fingerprint: str, captured: dict[str, Any]
    ) -> None:
        try:
            if captured["status"] >= 500 or len(captured["body"]) > MAX_STORED_BODY:
                await redis.delete(store_key)  # let the client retry
                return
            keep = {b"content-type", b"location", b"x-correlation-id"}
            await redis.set(
                store_key,
                json.dumps(
                    {
                        "state": "done",
                        "fp": fingerprint,
                        "status": captured["status"],
                        "headers": [
                            (k.decode(), v.decode())
                            for k, v in captured["headers"]
                            if k.lower() in keep
                        ],
                        "body": base64.b64encode(captured["body"]).decode(),
                    }
                ),
                ex=IDEMPOTENCY_TTL_S,
            )
        except RedisError:
            log.warning("idempotency result not stored")

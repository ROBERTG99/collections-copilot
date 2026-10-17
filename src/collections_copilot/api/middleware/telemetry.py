"""Outermost middleware (§14): correlation id, server span and the request duration histogram.

The correlation id comes from `X-Correlation-ID` (the BFF sends one) or is generated, and is
echoed on every response; problem details read the same value. The span continues the caller's
W3C `traceparent`. The histogram is labelled by route template, never by raw path.
"""

from __future__ import annotations

import time
import uuid

from fastapi.telemetry import TelemetryConfig
from opentelemetry import context, propagate
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from collections_copilot.observability import metrics, tracing
from collections_copilot.observability.logging import correlation_id

NATIVE_TELEMETRY_OFF: TelemetryConfig = {
    "tracing": False,
    "metrics": False,
    "logs": False,
    "operation_spans": False,
}
_SAFE_ID = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:")


def _cid(raw: str | None) -> str:
    if raw and len(raw) <= 128 and set(raw) <= _SAFE_ID:
        return raw
    return str(uuid.uuid4())


class TelemetryMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        cid = _cid(headers.get("x-correlation-id"))
        scope["cc.correlation_id"] = cid
        cid_token = correlation_id.set(cid)
        ctx_token = context.attach(propagate.extract(headers))
        method = scope["method"]
        started = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                raw = list(message.get("headers", []))
                if not any(k.lower() == b"x-correlation-id" for k, _ in raw):
                    raw.append((b"x-correlation-id", cid.encode()))
                message["headers"] = raw
            await send(message)

        span_cm = tracing.tracer().start_as_current_span(
            f"{method}", kind=tracing.SpanKind.SERVER, attributes={"http.request.method": method}
        )
        try:
            with span_cm as span:
                try:
                    await self.app(scope, receive, send_wrapper)
                finally:
                    route = _route(scope)
                    span.update_name(f"{method} {route}")
                    span.set_attributes(
                        {
                            "http.route": route,
                            "http.response.status_code": status,
                            "cc.correlation_id": cid,
                        }
                    )
                    if status >= 500:
                        span.set_status(tracing.Status(tracing.StatusCode.ERROR))
                    metrics.HTTP_DURATION.labels(method, route, str(status)).observe(
                        time.perf_counter() - started
                    )
        finally:
            context.detach(ctx_token)
            correlation_id.reset(cid_token)


def _route(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return str(path) if path else "unmatched"

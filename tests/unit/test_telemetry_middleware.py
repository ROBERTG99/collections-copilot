"""T-42: the API's outermost middleware sets correlation ids, continues traces and times routes."""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from prometheus_client import REGISTRY

from collections_copilot.api.middleware.telemetry import NATIVE_TELEMETRY_OFF, TelemetryMiddleware
from collections_copilot.observability import tracing

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"


def _app() -> FastAPI:
    app = FastAPI(telemetry=NATIVE_TELEMETRY_OFF)  # as create_app
    app.add_middleware(TelemetryMiddleware)

    @app.get("/api/v1/things/{thing_id}")
    async def thing(thing_id: str) -> dict[str, str]:
        return {"id": thing_id}

    return app


async def test_correlation_trace_and_route_template() -> None:
    spans = tracing.install_test_exporter()
    spans.clear()
    labels = {"method": "GET", "route": "/api/v1/things/{thing_id}", "status": "200"}
    before = REGISTRY.get_sample_value("cc_http_request_duration_seconds_count", labels) or 0
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        ok = await client.get(
            "/api/v1/things/abc",
            headers={
                "x-correlation-id": "corr-42",
                "traceparent": f"00-{TRACE_ID}-00f067aa0ba902b7-01",
            },
        )
        bad = await client.get("/api/v1/things/def", headers={"x-correlation-id": "a b\tc"})
    assert ok.headers["x-correlation-id"] == "corr-42"
    assert bad.headers["x-correlation-id"] != "a b\tc"  # unsafe ids are replaced
    assert len(bad.headers["x-correlation-id"]) == 36
    after = REGISTRY.get_sample_value("cc_http_request_duration_seconds_count", labels)
    assert after == before + 2  # labelled by template, not by raw path
    server = [s for s in spans.get_finished_spans() if s.name == "GET /api/v1/things/{thing_id}"]
    assert len(server) == 2
    assert f"{server[0].context.trace_id:032x}" == TRACE_ID  # continues the BFF's trace
    assert server[0].attributes["http.response.status_code"] == 200


PHONE = "+15550001234"


def _leaks(span: object) -> list[str]:
    """Everything a span exports that could carry text: status, attributes and events."""
    out = [str(span.status.description or "")]  # type: ignore[attr-defined]
    out += [str(v) for v in (span.attributes or {}).values()]  # type: ignore[attr-defined]
    for event in span.events:  # type: ignore[attr-defined]
        out += [event.name, *[str(v) for v in (event.attributes or {}).values()]]
    return [text for text in out if PHONE in text]


def test_span_errors_carry_the_exception_type_only() -> None:
    """M6 review B1: exception messages (debtor text, phone numbers) never reach a span."""
    import pytest

    spans = tracing.install_test_exporter()
    spans.clear()
    with pytest.raises(ValueError, match="twilio"), tracing.span("work"):
        raise ValueError(f"twilio rejected To={PHONE}")
    with pytest.raises(KeyError), tracing.consumer_span("process q", {}):
        raise KeyError(f"reply body mentions {PHONE}")
    finished = spans.get_finished_spans()
    assert [s.status.description for s in finished] == ["ValueError", "KeyError"]
    assert all(not s.events for s in finished)
    assert not [leak for s in finished for leak in _leaks(s)]


async def test_unhandled_errors_are_logged_with_ids_and_never_exported() -> None:
    """M6 review B1 and M2: the server span hides the message; the log line keeps the ids."""
    import io
    import json
    import logging

    from collections_copilot.observability.logging import JsonFormatter

    spans = tracing.install_test_exporter()
    spans.clear()
    app = FastAPI(telemetry=NATIVE_TELEMETRY_OFF)
    app.add_middleware(TelemetryMiddleware)

    @app.get("/boom")
    async def boom() -> None:
        raise ValueError(f"reply text: we pay friday, call {PHONE}")

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(service="api"))
    logger = logging.getLogger("collections_copilot.api.middleware.telemetry")
    logger.addHandler(handler)
    try:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/boom", headers={"x-correlation-id": "corr-500"})
    finally:
        logger.removeHandler(handler)
    assert resp.status_code == 500
    server = [sp for sp in spans.get_finished_spans() if sp.name == "GET /boom"]
    assert len(server) == 1
    assert server[0].status.description == "ValueError"
    assert not _leaks(server[0])
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [line["msg"] for line in lines] == ["unhandled error"]
    assert lines[0]["correlation_id"] == "corr-500"
    assert lines[0]["trace_id"] == f"{server[0].context.trace_id:032x}"
    assert PHONE not in stream.getvalue()  # the traceback is masked too

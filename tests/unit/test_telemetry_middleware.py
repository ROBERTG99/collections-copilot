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

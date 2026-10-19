"""T-29 / FR-11: SSE progress from the case's Redis stream, with Last-Event-ID resume."""

from __future__ import annotations

import asyncio
import socket
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import uvicorn
from redis.asyncio import Redis

from collections_copilot.observability.events import RedisEventPublisher, stream_key
from tests.factories import make_account
from tests.integration.graph_support import new_case

pytestmark = pytest.mark.integration


@pytest.fixture
async def server(api_app: Any) -> AsyncIterator[str]:
    """A real HTTP server: SSE responses never end, so the ASGI test transport cannot read them."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = uvicorn.Config(
        api_app, host="127.0.0.1", port=port, lifespan="off", log_level="warning"
    )
    srv = uvicorn.Server(config)
    task = asyncio.create_task(srv.serve())
    while not srv.started:
        await asyncio.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        srv.should_exit = True
        await task


@pytest.fixture
async def redis(test_settings: Any) -> AsyncIterator[Redis]:
    client = Redis.from_url(test_settings.redis_url)
    yield client
    await client.aclose()


async def read_events(
    base: str,
    url: str,
    headers: dict[str, str],
    count: int,
    timeout_s: float = 5,
    until: str | None = None,
) -> list[dict[str, str]]:
    """Read SSE frames until `count` non-control events (heartbeats included) arrive."""
    events: list[dict[str, str]] = []
    frame: dict[str, str] = {}
    async with (
        httpx.AsyncClient(base_url=base, timeout=timeout_s) as client,
        client.stream("GET", url, headers=headers) as resp,
    ):
        assert resp.status_code == 200, await resp.aread()
        assert resp.headers["content-type"].startswith("text/event-stream")
        async for line in resp.aiter_lines():
            if line == "":
                if "event" in frame:
                    events.append(frame)
                    done = frame["event"] == until if until else len(events) >= count
                    if done:
                        return events
                frame = {}
                continue
            key, _, value = line.partition(": ")
            frame[key] = value
    return events


async def test_resume_with_last_event_id(
    server: str, redis: Redis, sessionmaker: Any, tenant: uuid.UUID, auth: Any
) -> None:
    case_id = await new_case(sessionmaker, tenant, await make_account(sessionmaker, tenant))
    await redis.delete(stream_key(str(tenant), case_id))
    publisher = RedisEventPublisher(redis)
    for n in range(3):
        await publisher.publish(str(tenant), case_id, "node", {"node": f"n{n}"})
    url = f"/api/v1/cases/{case_id}/events"
    first = await read_events(server, url, auth("analyst"), 3)
    assert [e["event"] for e in first] == ["node", "node", "node"]
    assert ['"n0"' in e["data"] for e in first] == [True, False, False]
    last_seen = first[-1]["id"]

    await publisher.publish(str(tenant), case_id, "status", {"status": "awaiting_approval"})
    await publisher.publish(str(tenant), case_id, "draft", {"version": 1})
    resumed = await read_events(server, url, {**auth("analyst"), "Last-Event-ID": last_seen}, 2)
    assert [e["event"] for e in resumed] == ["status", "draft"]
    assert all(e["id"] > last_seen for e in resumed)


async def test_live_events_and_heartbeat(
    server: str, api_app: Any, redis: Redis, sessionmaker: Any, tenant: uuid.UUID, auth: Any
) -> None:
    case_id = await new_case(sessionmaker, tenant, await make_account(sessionmaker, tenant))
    await redis.delete(stream_key(str(tenant), case_id))
    original = api_app.state.settings.sse_heartbeat_s
    api_app.state.settings.sse_heartbeat_s = 0.2
    try:
        reader = asyncio.create_task(
            read_events(server, f"/api/v1/cases/{case_id}/events", auth("analyst"), 0, until="node")
        )
        await asyncio.sleep(0.5)
        await RedisEventPublisher(redis).publish(str(tenant), case_id, "node", {"node": "draft"})
        events = await reader
    finally:
        api_app.state.settings.sse_heartbeat_s = original
    names = [e["event"] for e in events]
    assert names[0] == "heartbeat"  # silence → heartbeat after 0.2 s
    assert "id" not in events[0]  # heartbeats never move Last-Event-ID
    assert names[-1] == "node"  # a live event arrives on the open stream
    assert '"draft"' in events[-1]["data"]


async def test_other_tenant_gets_404(
    server: str, sessionmaker: Any, tenant: uuid.UUID, auth: Any
) -> None:
    case_id = await new_case(sessionmaker, tenant, await make_account(sessionmaker, tenant))
    async with httpx.AsyncClient(base_url=server) as client:
        resp = await client.get(
            f"/api/v1/cases/{case_id}/events", headers=auth("analyst", tenant_id=uuid.uuid4())
        )
    assert resp.status_code == 404

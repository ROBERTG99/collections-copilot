"""T-33 / NFR-9: size limits, rate limits and Idempotency-Key, all as problem details."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from tests.factories import make_account

pytestmark = pytest.mark.integration


@pytest.fixture
def low_rate_limit(api_app: Any) -> Iterator[int]:
    settings = api_app.state.settings
    saved = settings.rate_limit_per_minute
    settings.rate_limit_per_minute = 5
    try:
        yield 5
    finally:
        settings.rate_limit_per_minute = saved


async def test_upload_over_10mb_413(client: httpx.AsyncClient, auth) -> None:  # type: ignore[no-untyped-def]
    resp = await client.post(
        "/api/v1/ingest/uploads",
        json={"filename": "aging.csv", "size_bytes": 10 * 1024 * 1024 + 1},
        headers=auth("analyst"),
    )
    assert resp.status_code == 413
    assert resp.headers["content-type"].startswith("application/problem+json")
    big = json.dumps({"rows": [{"pad": "x" * 1000}] * 1100})  # > 1 MB of JSON
    resp = await client.post(
        "/api/v1/ingest/aging",
        content=big,
        headers={**auth("analyst"), "Content-Type": "application/json"},
    )
    assert resp.status_code == 413
    assert resp.json()["correlation_id"]

    async def chunks() -> Any:  # no Content-Length: counted while streaming
        for _ in range(20):
            yield b"x" * 100_000

    resp = await client.post(
        "/api/v1/ingest/aging",
        content=chunks(),
        headers={**auth("analyst"), "Content-Type": "application/json"},
    )
    assert resp.status_code == 413


async def test_rate_limit_429(client: httpx.AsyncClient, auth, low_rate_limit: int) -> None:  # type: ignore[no-untyped-def]
    user = auth("analyst", username=f"rl-{uuid.uuid4().hex[:8]}")
    codes = [(await client.get("/api/v1/me", headers=user)).status_code for _ in range(6)]
    assert codes == [200] * 5 + [429]
    limited = await client.get("/api/v1/me", headers=user)
    assert limited.status_code == 429
    assert 1 <= int(limited.headers["retry-after"]) <= 60
    assert limited.json()["code"] == "rate_limited"
    other = auth("analyst", username=f"rl-{uuid.uuid4().hex[:8]}")
    ok = await client.get("/api/v1/me", headers=other)
    assert ok.status_code == 200
    assert ok.headers["x-ratelimit-remaining"] == "4"
    assert (await client.get("/healthz")).status_code == 200  # exempt


async def test_idempotency_key_replays(
    client: httpx.AsyncClient, sessionmaker, tenant, auth
) -> None:  # type: ignore[no-untyped-def]
    await make_account(sessionmaker, tenant)
    key = f"opt-{uuid.uuid4().hex}"
    body = {"phone_e164": "+15550001234"}
    headers = {**auth("analyst"), "Idempotency-Key": key}
    first = await client.post("/api/v1/opt-outs", json=body, headers=headers)
    second = await client.post("/api/v1/opt-outs", json=body, headers=headers)
    assert first.status_code == second.status_code == 201
    assert second.headers["idempotent-replayed"] == "true"
    assert second.json() == first.json()  # created=True both times: the first result replayed
    reused = await client.post(
        "/api/v1/opt-outs", json={"phone_e164": "+15550004321"}, headers=headers
    )
    assert reused.status_code == 422
    assert reused.json()["code"] == "idempotency_key_reused"
    bad = await client.post(
        "/api/v1/opt-outs", json=body, headers={**auth("analyst"), "Idempotency-Key": "a b"}
    )
    assert bad.status_code == 422


async def test_nul_characters_are_422_not_500(client: httpx.AsyncClient, auth) -> None:  # type: ignore[no-untyped-def]
    """Found by Schemathesis: Postgres text and JSONB reject NUL, which used to surface as 500."""
    resp = await client.post(
        "/api/v1/ingest/aging",
        content=b'{"rows": [{"name": "a\\u0000b"}]}',
        headers={**auth("analyst"), "Content-Type": "application/json"},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "nul_character"
    query = await client.get("/api/v1/queue", params={"q": "a\x00b"}, headers=auth("analyst"))
    assert query.status_code == 422
    escaped = await client.post(
        "/api/v1/opt-outs",
        content=b'{"phone_e164": "+15550001234", "reason": "path C:\\\\u0000dir"}',
        headers={**auth("analyst"), "Content-Type": "application/json"},
    )
    assert escaped.status_code == 201  # an escaped backslash followed by "u0000" is plain text


async def test_random_tokens_share_the_ip_bucket(
    client: httpx.AsyncClient, api_app: Any, low_rate_limit: int
) -> None:
    """M3 security review: an unverifiable bearer token is not an identity of its own."""
    async for key in api_app.state.redis.scan_iter(match="cc:rl:ip:*"):
        await api_app.state.redis.delete(key)  # earlier unauthenticated tests share this bucket
    codes = []
    for _ in range(6):
        junk = {"Authorization": f"Bearer junk-{uuid.uuid4().hex}"}
        codes.append((await client.get("/api/v1/me", headers=junk)).status_code)
    assert codes[:5] == [401] * 5
    assert codes[5] == 429


async def test_unauthenticated_results_are_not_stored(client: httpx.AsyncClient, auth) -> None:  # type: ignore[no-untyped-def]
    key = f"k-{uuid.uuid4().hex}"
    body = {"phone_e164": "+15550004444"}
    denied = await client.post(
        "/api/v1/opt-outs",
        json=body,
        headers={"Authorization": "Bearer junk", "Idempotency-Key": key},
    )
    assert denied.status_code == 401
    ok = await client.post(
        "/api/v1/opt-outs", json=body, headers={**auth("analyst"), "Idempotency-Key": key}
    )
    assert ok.status_code in (200, 201)
    assert "idempotent-replayed" not in ok.headers

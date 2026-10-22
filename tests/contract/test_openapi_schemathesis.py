"""Schemathesis finds zero server errors across every /api/v1 operation.

The API runs in-process on a free port with real Postgres, Redis and MinIO, and in-memory doubles
for the queue, SMS, mail and gateway admin, so generated requests never reach the compose worker
or any real recipient. Requests are signed as a supervisor of a freshly created tenant.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
import schemathesis
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from hypothesis import HealthCheck, settings
from schemathesis import checks

from collections_copilot.api.deps.auth import StaticKeyVerifier
from collections_copilot.api.main import create_app
from collections_copilot.config import Settings
from collections_copilot.db.models import Tenant
from collections_copilot.db.session import make_engine, make_sessionmaker
from collections_copilot.integrations.email.smtp import InMemoryNotifier
from collections_copilot.integrations.sms.base import InMemorySmsSender

pytestmark = [pytest.mark.contract, pytest.mark.integration]
ISSUER, AUDIENCE, KID = "https://issuer.test", "cc-web", "schemathesis"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
TENANT = uuid.uuid4()


class NullQueue:
    async def send(self, queue: str, body: dict[str, Any], **_: Any) -> str:
        return "m"

    async def depth(self, queue: str) -> int:
        return 0


class NullGatewayAdmin:
    async def decide(
        self, tenant: str, approval_id: str, decision: str, note: str
    ) -> dict[str, Any]:
        return {"status": "approved" if decision == "approve" else "rejected"}

    async def get(self, tenant: str, approval_id: str) -> dict[str, Any]:
        return {"status": "pending"}


def _settings(database_url: str) -> Settings:
    return Settings(
        env="test",
        database_url=database_url,
        oidc_issuer=ISSUER,
        oidc_audience=AUDIENCE,
        s3_bucket="cc-uploads-test",
        rate_limit_per_minute=1_000_000,
    )


def build_app(database_url: str) -> Any:
    return create_app(
        _settings(database_url),
        token_verifier=StaticKeyVerifier(ISSUER, AUDIENCE, KID, KEY.public_key()),
        queues=NullQueue(),  # type: ignore[arg-type]
        sms_sender=InMemorySmsSender(),
        gateway_admin=NullGatewayAdmin(),  # type: ignore[arg-type]
        notifier=InMemoryNotifier(),
    )


# The schema is static: build it once from an app that is never started.
schema = schemathesis.openapi.from_dict(build_app("postgresql+psycopg://unused/unused").openapi())


def token(roles: list[str]) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "sub-sam",
        "cognito:username": "sam.supervisor",
        "cognito:groups": roles,
        "custom:tenant_id": str(TENANT),
        "iat": now,
        "exp": now + 3600,
        "token_use": "access",
    }
    return jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": KID})


@pytest.fixture(scope="module")
def server(database_url: str) -> Iterator[str]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = build_app(database_url)

    async def prepare() -> None:
        engine = make_engine(database_url, pool_size=1)
        async with make_sessionmaker(engine)() as s, s.begin():
            s.add(Tenant(id=TENANT, name=f"schemathesis-{TENANT.hex[:8]}"))
        await engine.dispose()

    asyncio.run(prepare())
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not srv.started:
        assert time.monotonic() < deadline, "API did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=10)


@schema.include(path_regex="^/api/v1/(?!webhooks/)").parametrize()
@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
        HealthCheck.filter_too_much,
    ],
)
def test_no_server_errors(case: Any, server: str) -> None:
    roles = (
        ["analyst"] if case.path == "/api/v1/cases" and case.method == "POST" else ["supervisor"]
    )
    response = case.call(base_url=server, headers={"Authorization": f"Bearer {token(roles)}"})
    case.validate_response(response, checks=[checks.not_a_server_error])


@schema.include(path_regex="^/api/v1/webhooks/").parametrize()
@settings(max_examples=15, deadline=None, suppress_health_check=list(HealthCheck))
def test_webhooks_never_500(case: Any, server: str) -> None:
    response = case.call(base_url=server)
    case.validate_response(response, checks=[checks.not_a_server_error])

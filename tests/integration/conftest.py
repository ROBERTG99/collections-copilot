"""Integration fixtures: an in-process API wired to the compose dependencies."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import jwt
import pytest
from asgi_lifespan import LifespanManager
from cryptography.hazmat.primitives.asymmetric import rsa

from collections_copilot.api.deps.auth import StaticKeyVerifier
from collections_copilot.api.main import create_app
from collections_copilot.config import Settings
from tests.factories import make_tenant

ISSUER = "http://dev-oidc.test"
AUDIENCE = "cc-web"
KID = "test-key"


@pytest.fixture(scope="session")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


TokenFactory = Callable[..., str]


@pytest.fixture(scope="session")
def make_token(signing_key: rsa.RSAPrivateKey) -> TokenFactory:
    def factory(
        tenant_id: uuid.UUID, roles: list[str], username: str = "ana.analyst", **extra: Any
    ) -> str:
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": f"sub-{username}",
            "cognito:username": username,
            "cognito:groups": roles,
            "custom:tenant_id": str(tenant_id),
            "iat": now,
            "exp": now + 600,
            **extra,
        }
        return jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": KID})

    return factory


@pytest.fixture(scope="session")
def test_settings(database_url: str) -> Settings:
    return Settings(
        env="test",
        database_url=database_url,
        oidc_issuer=ISSUER,
        oidc_audience=AUDIENCE,
        s3_bucket="cc-uploads-test",
    )


@pytest.fixture(scope="session")
async def api_app(test_settings: Settings, signing_key: rsa.RSAPrivateKey) -> AsyncIterator[Any]:
    verifier = StaticKeyVerifier(ISSUER, AUDIENCE, KID, signing_key.public_key())
    app = create_app(test_settings, token_verifier=verifier)
    async with LifespanManager(app):
        yield app  # lifespan populates app.state; requests go straight to the FastAPI app


@pytest.fixture
async def client(api_app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=api_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as c:
        yield c


@pytest.fixture
async def tenant(sessionmaker: Any) -> uuid.UUID:
    return await make_tenant(sessionmaker)


@pytest.fixture
def auth(make_token: TokenFactory, tenant: uuid.UUID) -> Callable[..., dict[str, str]]:
    def headers(
        *roles: str, username: str = "ana.analyst", tenant_id: uuid.UUID | None = None
    ) -> dict[str, str]:
        token = make_token(tenant_id or tenant, list(roles or ("analyst",)), username)
        return {"Authorization": f"Bearer {token}"}

    return headers

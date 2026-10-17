"""OIDC verification rejects wrong issuer/audience, expired, unsigned and HS256 tokens (FR-13)."""

from __future__ import annotations

import time
import uuid
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from collections_copilot.api.deps.auth import StaticKeyVerifier, principal_from_claims
from collections_copilot.api.errors import ProblemError

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
VERIFIER = StaticKeyVerifier("http://issuer.test", "cc-web", "k1", KEY.public_key())


def token(**overrides: Any) -> str:
    now = int(time.time())
    claims = {
        "iss": "http://issuer.test",
        "aud": "cc-web",
        "sub": "u1",
        "iat": now,
        "exp": now + 60,
        "token_use": "access",
    }
    claims.update(overrides)
    return jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": "k1"})


async def test_valid_token() -> None:
    claims = await VERIFIER.verify(token())
    assert claims["sub"] == "u1"


async def test_cognito_access_token_client_id_accepted() -> None:
    now = int(time.time())
    access = jwt.encode(
        {
            "iss": "http://issuer.test",
            "client_id": "cc-web",
            "sub": "u1",
            "iat": now,
            "exp": now + 60,
            "token_use": "access",
        },
        KEY,
        algorithm="RS256",
        headers={"kid": "k1"},
    )
    assert (await VERIFIER.verify(access))["client_id"] == "cc-web"


@pytest.mark.parametrize(
    "bad",
    [
        {"iss": "http://evil.test"},
        {"aud": "other-client"},
        {"exp": int(time.time()) - 3600},
    ],
)
async def test_rejects_bad_claims(bad: dict[str, Any]) -> None:
    with pytest.raises(jwt.PyJWTError):
        await VERIFIER.verify(token(**bad))


async def test_rejects_unsigned_and_hs256() -> None:
    now = int(time.time())
    claims = {
        "iss": "http://issuer.test",
        "aud": "cc-web",
        "sub": "u1",
        "iat": now,
        "exp": now + 60,
    }
    none_alg = jwt.encode(claims, None, algorithm="none", headers={"kid": "k1"})
    with pytest.raises(jwt.PyJWTError):
        await VERIFIER.verify(none_alg)
    hs = jwt.encode(claims, "x" * 40, algorithm="HS256", headers={"kid": "k1"})
    with pytest.raises(jwt.PyJWTError):
        await VERIFIER.verify(hs)


def test_principal_requires_tenant_and_maps_roles() -> None:
    tid = uuid.uuid4()
    p = principal_from_claims(
        {"sub": "s", "custom:tenant_id": str(tid), "cognito:groups": ["analyst", "other"]}
    )
    assert p.tenant_id == tid
    assert p.roles == frozenset({"analyst"})
    with pytest.raises(ProblemError):
        principal_from_claims({"sub": "s", "cognito:groups": ["analyst"]})

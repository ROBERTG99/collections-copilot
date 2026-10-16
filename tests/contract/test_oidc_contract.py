"""TokenVerifier contract: dev-oidc behaves like the Cognito surface the API relies on."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import uvicorn
from dev_oidc.app import TENANT_A, TENANT_B, Issuer, create_app

from collections_copilot.api.deps.auth import JwksTokenVerifier, principal_from_claims
from tests.integration.ledger_stack import free_port

pytestmark = pytest.mark.contract
REDIRECT = "http://localhost:8300/auth/callback"


@pytest.fixture(scope="module")
async def oidc() -> AsyncIterator[str]:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    app = create_app(Issuer(issuer=base, client_ids=("cc-web",)))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    yield base
    server.should_exit = True
    await task


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return verifier, challenge


async def login(client: httpx.AsyncClient, base: str, username: str) -> dict[str, str]:
    verifier, challenge = pkce()
    params = {
        "response_type": "code",
        "client_id": "cc-web",
        "redirect_uri": REDIRECT,
        "state": "s1",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "nonce": "n1",
    }
    page = await client.get(f"{base}/authorize", params=params)
    assert page.status_code == 200
    assert username in page.text
    form = {
        k: v
        for k, v in params.items()
        if k in ("client_id", "redirect_uri", "state", "code_challenge", "nonce")
    }
    redirect = await client.post(f"{base}/authorize", data={**form, "username": username})
    assert redirect.status_code == 302
    query = parse_qs(urlsplit(redirect.headers["location"]).query)
    assert query["state"] == ["s1"]
    tokens = await client.post(
        f"{base}/token",
        data={
            "grant_type": "authorization_code",
            "client_id": "cc-web",
            "code": query["code"][0],
            "redirect_uri": REDIRECT,
            "code_verifier": verifier,
        },
    )
    assert tokens.status_code == 200, tokens.text
    return dict(tokens.json())


async def test_discovery_and_jwks(oidc: str) -> None:
    async with httpx.AsyncClient() as client:
        conf = (await client.get(f"{oidc}/.well-known/openid-configuration")).json()
        assert conf["issuer"] == oidc
        assert conf["code_challenge_methods_supported"] == ["S256"]
        keys = (await client.get(conf["jwks_uri"])).json()["keys"]
        assert keys[0]["alg"] == "RS256"
        assert keys[0]["kid"]


@pytest.mark.parametrize(
    ("username", "tenant", "role"),
    [
        ("ana.analyst", TENANT_A, "analyst"),
        ("sam.supervisor", TENANT_A, "supervisor"),
        ("adi.admin", TENANT_A, "admin"),
        ("tia.tenantb", TENANT_B, "analyst"),
    ],
)
async def test_code_flow_tokens_verify_like_cognito(
    oidc: str, username: str, tenant: str, role: str
) -> None:
    async with httpx.AsyncClient() as client:
        tokens = await login(client, oidc, username)
    verifier = JwksTokenVerifier(oidc, "cc-web")
    for kind in ("id_token", "access_token"):
        claims = await verifier.verify(tokens[kind])
        principal = principal_from_claims(claims)
        assert str(principal.tenant_id) == tenant
        assert principal.roles == frozenset({role})
        assert principal.username == username


async def test_pkce_and_code_reuse_rejected(oidc: str) -> None:
    async with httpx.AsyncClient() as client:
        verifier, challenge = pkce()
        form = {
            "client_id": "cc-web",
            "redirect_uri": REDIRECT,
            "state": "s",
            "code_challenge": challenge,
            "nonce": "",
            "username": "ana.analyst",
        }
        redirect = await client.post(f"{oidc}/authorize", data=form)
        code = parse_qs(urlsplit(redirect.headers["location"]).query)["code"][0]
        bad = await client.post(
            f"{oidc}/token",
            data={
                "grant_type": "authorization_code",
                "client_id": "cc-web",
                "code": code,
                "redirect_uri": REDIRECT,
                "code_verifier": "wrong",
            },
        )
        assert bad.status_code == 400
        reused = await client.post(
            f"{oidc}/token",
            data={
                "grant_type": "authorization_code",
                "client_id": "cc-web",
                "code": code,
                "redirect_uri": REDIRECT,
                "code_verifier": verifier,
            },
        )
        assert reused.status_code == 400  # single use, even after a failed attempt
        unknown = await client.get(
            f"{oidc}/authorize",
            params={
                "response_type": "code",
                "client_id": "evil",
                "redirect_uri": REDIRECT,
                "state": "s",
                "code_challenge": "x",
            },
        )
        assert unknown.status_code == 400


async def test_refresh_token_rotates(oidc: str) -> None:
    async with httpx.AsyncClient() as client:
        tokens = await login(client, oidc, "sam.supervisor")
        fresh = await client.post(
            f"{oidc}/token",
            data={
                "grant_type": "refresh_token",
                "client_id": "cc-web",
                "refresh_token": tokens["refresh_token"],
            },
        )
        assert fresh.status_code == 200
        replay = await client.post(
            f"{oidc}/token",
            data={
                "grant_type": "refresh_token",
                "client_id": "cc-web",
                "refresh_token": tokens["refresh_token"],
            },
        )
        assert replay.status_code == 400

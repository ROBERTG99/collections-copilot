"""M7 Terraform review: mcp-gateway verifies KMS-signed agent tokens with the key set the API
publishes at /.well-known/agent-jwks.json (configured as MCPGW_JWKS_URL)."""

from __future__ import annotations

import jwt
from fastapi.testclient import TestClient
from moto import mock_aws

from collections_copilot.api.main import create_app
from collections_copilot.config import Settings
from collections_copilot.integrations.mcp import build_token_provider

OFFLINE_DB = "postgresql+psycopg://x:x@127.0.0.1:1/x"


def test_kms_mode_publishes_the_signing_key() -> None:
    import asyncio

    import boto3

    with mock_aws():
        kms = boto3.client("kms", region_name="us-east-1")
        key_id = kms.create_key(KeySpec="RSA_2048", KeyUsage="SIGN_VERIFY")["KeyMetadata"]["KeyId"]
        settings = Settings(
            database_url=OFFLINE_DB,
            aws_region="us-east-1",
            agent_token_provider="kms",
            agent_kms_key_id=key_id,
        )
        with TestClient(create_app(settings)) as client:
            resp = client.get("/.well-known/agent-jwks.json")
        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "public, max-age=300"
        [jwk] = resp.json()["keys"]
        token = asyncio.run(
            build_token_provider(settings, "cc-api").token("t-1", ["mcpgw:approve"])
        )
    claims = jwt.decode(token, jwt.PyJWK(jwk).key, algorithms=["RS256"], audience="mcp-gateway")
    assert jwk["kid"] == key_id
    assert claims["sub"] == "cc-api"


def test_dev_issuer_mode_publishes_no_keys() -> None:
    with TestClient(create_app(Settings(database_url=OFFLINE_DB))) as client:
        resp = client.get("/.well-known/agent-jwks.json")
    assert resp.status_code == 200
    assert resp.json() == {"keys": []}

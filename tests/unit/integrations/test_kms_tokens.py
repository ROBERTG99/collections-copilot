"""Agent JWTs signed by KMS verify against the published JWKS (moto, offline)."""

from __future__ import annotations

import jwt
from moto import mock_aws

from collections_copilot.integrations.mcp.tokens import KmsTokenProvider


async def test_kms_token_verifies_with_jwks() -> None:
    import boto3

    with mock_aws():
        await _check(boto3)


async def _check(boto3) -> None:  # type: ignore[no-untyped-def]
    kms = boto3.client("kms", region_name="us-east-1")
    key_id = kms.create_key(KeySpec="RSA_2048", KeyUsage="SIGN_VERIFY")["KeyMetadata"]["KeyId"]
    provider = KmsTokenProvider(key_id=key_id, issuer="collections-copilot", region="us-east-1")
    provider._client = kms
    token = await provider.token("tenant-1", ["ledger:write"])
    jwk = provider.jwks()["keys"][0]
    assert jwt.get_unverified_header(token)["kid"] == key_id
    claims = jwt.decode(
        token,
        jwt.PyJWK(jwk).key,
        algorithms=["RS256"],
        audience="mcp-gateway",
        issuer="collections-copilot",
    )
    assert claims["sub"] == "cc-worker"
    assert claims["tenant"] == "tenant-1"
    assert claims["scopes"] == ["ledger:write"]
    assert claims["exp"] - claims["iat"] <= 300

"""Agent JWTs for mcp-gateway (§4.4): iss, aud=mcp-gateway, sub=cc-worker, tenant, scopes, exp ≤ 5 min.

DevIssuerTokenProvider asks the gateway's dev issuer (local only). KmsTokenProvider signs with an
AWS KMS RSA_2048 key (RSASSA_PKCS1_V1_5_SHA_256 = RS256); its JWKS is served by the API at
/.well-known/agent-jwks.json.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

TOKEN_TTL_S = 300
REFRESH_MARGIN_S = 60


class AgentTokenProvider(Protocol):
    async def token(self, tenant: str, scopes: list[str]) -> str: ...


@dataclass
class DevIssuerTokenProvider:
    issuer_url: str
    subject: str = "cc-worker"
    _cache: dict[tuple[str, tuple[str, ...]], tuple[str, float]] = field(default_factory=dict)

    async def token(self, tenant: str, scopes: list[str]) -> str:
        key = (tenant, tuple(sorted(scopes)))
        cached = self._cache.get(key)
        if cached and cached[1] - time.time() > REFRESH_MARGIN_S:
            return cached[0]
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                f"{self.issuer_url.rstrip('/')}/token",
                json={
                    "sub": self.subject,
                    "tenant": tenant,
                    "scopes": scopes,
                    "ttl_s": TOKEN_TTL_S,
                },
            )
            resp.raise_for_status()
        token = str(resp.json()["access_token"])
        self._cache[key] = (token, time.time() + TOKEN_TTL_S)
        return token


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@dataclass
class KmsTokenProvider:
    """RS256 tokens signed by KMS (`kms:Sign` on the signing key only, per the IAM module)."""

    key_id: str
    issuer: str
    region: str
    subject: str = "cc-worker"
    endpoint_url: str | None = None
    _client: Any = None

    def _kms(self) -> Any:
        if self._client is None:
            import boto3

            self._client = boto3.client(
                "kms", region_name=self.region, endpoint_url=self.endpoint_url
            )
        return self._client

    def _sign(self, tenant: str, scopes: list[str]) -> str:
        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT", "kid": self.key_id}
        claims = {
            "iss": self.issuer,
            "aud": "mcp-gateway",
            "sub": self.subject,
            "tenant": tenant,
            "scopes": scopes,
            "iat": now,
            "exp": now + TOKEN_TTL_S,
            "jti": uuid.uuid4().hex,
        }
        signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claims).encode())}"
        sig = self._kms().sign(
            KeyId=self.key_id,
            Message=signing_input.encode(),
            MessageType="RAW",
            SigningAlgorithm="RSASSA_PKCS1_V1_5_SHA_256",
        )["Signature"]
        return f"{signing_input}.{_b64(sig)}"

    async def token(self, tenant: str, scopes: list[str]) -> str:
        return await asyncio.to_thread(self._sign, tenant, scopes)

    def jwks(self) -> dict[str, list[dict[str, str]]]:
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives.serialization import load_der_public_key

        der = self._kms().get_public_key(KeyId=self.key_id)["PublicKey"]
        key = load_der_public_key(der)
        assert isinstance(key, rsa.RSAPublicKey)
        nums = key.public_numbers()

        def b64int(n: int) -> str:
            return _b64(n.to_bytes((n.bit_length() + 7) // 8, "big"))

        return {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": self.key_id,
                    "use": "sig",
                    "alg": "RS256",
                    "n": b64int(nums.n),
                    "e": b64int(nums.e),
                }
            ]
        }

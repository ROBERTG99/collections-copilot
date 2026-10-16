"""Agent JWT verification (RS256, aud=mcp-gateway, kid required) and the dev issuer."""

from __future__ import annotations

import asyncio
import base64
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

AUDIENCE = "mcp-gateway"
MAX_LIFETIME_S = 3600
LEEWAY_S = 30


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class Claims:
    sub: str
    tenant: str
    scopes: frozenset[str]
    raw: dict[str, Any]


def _b64(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def public_jwk(key: rsa.RSAPublicKey, kid: str) -> dict[str, str]:
    nums = key.public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _b64(nums.n),
        "e": _b64(nums.e),
    }


class DevIssuer:
    """Signs short-lived agent tokens for local development (never used in AWS)."""

    def __init__(self, issuer: str, private_key_pem: bytes | None = None) -> None:
        self.issuer = issuer
        if private_key_pem:
            loaded = serialization.load_pem_private_key(private_key_pem, password=None)
            assert isinstance(loaded, rsa.RSAPrivateKey)
            self.key = loaded
        else:
            self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = f"dev-{uuid.uuid4().hex[:12]}"

    def jwks(self) -> dict[str, list[dict[str, str]]]:
        return {"keys": [public_jwk(self.key.public_key(), self.kid)]}

    def issue(self, sub: str, tenant: str, scopes: list[str], ttl_s: int = 300) -> str:
        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "aud": AUDIENCE,
            "sub": sub,
            "tenant": tenant,
            "scopes": scopes,
            "iat": now,
            "exp": now + min(ttl_s, MAX_LIFETIME_S),
            "jti": uuid.uuid4().hex,
        }
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": self.kid})


class Verifier:
    def __init__(
        self,
        jwks_urls: tuple[str, ...],
        local: DevIssuer | None = None,
        issuers: tuple[str, ...] = ("mcp-gateway dev-issuer", "collections-copilot"),
    ) -> None:
        self.jwks_urls = jwks_urls
        self.local = local
        self.issuers = list(issuers)
        self._keys: dict[str, Any] = {}
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def _refresh(self) -> None:
        async with self._lock:
            if time.monotonic() - self._fetched_at < 5:  # rate-limit refreshes (stampede guard)
                return
            await self._refresh_locked()

    async def _refresh_locked(self) -> None:
        keys: dict[str, Any] = dict(self._keys)  # keep the last good keys if a fetch fails
        if self.local:
            keys[self.local.kid] = self.local.key.public_key()
        async with httpx.AsyncClient(timeout=5) as client:
            for url in self.jwks_urls:
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                except httpx.HTTPError:
                    continue
                for jwk in resp.json().get("keys", []):
                    if jwk.get("kty") == "RSA" and jwk.get("kid"):
                        keys[jwk["kid"]] = jwt.PyJWK(jwk).key
        self._keys = keys
        self._fetched_at = time.monotonic()

    async def verify(self, token: str) -> Claims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthError("malformed token") from exc
        if header.get("alg") != "RS256":
            raise AuthError("alg must be RS256")
        kid = header.get("kid")
        if not kid:
            raise AuthError("kid required")
        if kid not in self._keys or time.monotonic() - self._fetched_at > 300:
            await self._refresh()
        key = self._keys.get(kid)
        if key is None:
            raise AuthError("unknown kid")
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=AUDIENCE,
                issuer=self.issuers,
                leeway=LEEWAY_S,
                options={"require": ["exp", "iat", "iss", "sub", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(str(exc)) from exc
        if int(claims["exp"]) - int(claims["iat"]) > MAX_LIFETIME_S:
            raise AuthError("token lifetime exceeds 1 h")
        tenant = claims.get("tenant")
        if not tenant:
            raise AuthError("tenant claim required")
        return Claims(
            sub=claims["sub"],
            tenant=str(tenant),
            scopes=frozenset(claims.get("scopes") or []),
            raw=claims,
        )

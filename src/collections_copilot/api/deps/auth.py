"""OIDC bearer authentication and RBAC (FR-13).

Roles come from `cognito:groups`, the tenant from `custom:tenant_id`. Tokens are verified against
the issuer's JWKS (Cognito in AWS, dev-oidc locally): RS256 only, issuer and audience/client_id checked.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx
import jwt
from fastapi import Depends, Request

from collections_copilot.api.errors import ProblemError

Role = Literal["analyst", "supervisor", "admin"]
ROLES: tuple[Role, ...] = ("analyst", "supervisor", "admin")


@dataclass(frozen=True)
class Principal:
    sub: str
    username: str
    tenant_id: uuid.UUID
    roles: frozenset[str]

    def has(self, *roles: str) -> bool:
        return bool(self.roles & set(roles))


class TokenVerifier(Protocol):
    async def verify(self, token: str) -> dict[str, Any]: ...


class JwksTokenVerifier:
    """Verifies RS256 JWTs against a JWKS URL with a small key cache."""

    def __init__(self, issuer: str, audience: str, jwks_url: str | None = None) -> None:
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.jwks_url = jwks_url or f"{self.issuer}/.well-known/jwks.json"
        self._keys: dict[str, Any] = {}
        self._fetched = 0.0

    async def _refresh(self) -> None:
        if time.monotonic() - self._fetched < 5:
            return
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(self.jwks_url)
            resp.raise_for_status()
        keys = dict(self._keys)
        for jwk in resp.json().get("keys", []):
            if jwk.get("kty") == "RSA" and jwk.get("kid"):
                keys[jwk["kid"]] = jwt.PyJWK(jwk).key
        self._keys = keys
        self._fetched = time.monotonic()

    async def verify(self, token: str) -> dict[str, Any]:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not header.get("kid"):
            raise jwt.InvalidTokenError("RS256 with kid required")
        if header["kid"] not in self._keys:
            await self._refresh()
        key = self._keys.get(header["kid"])
        if key is None:
            raise jwt.InvalidTokenError("unknown kid")
        claims: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=self.issuer,
            options={"verify_aud": False, "require": ["exp", "iat", "iss", "sub"]},
            leeway=30,
        )
        # Cognito access tokens carry client_id instead of aud; ID tokens carry aud.
        aud = claims.get("aud") or claims.get("client_id")
        auds = aud if isinstance(aud, list) else [aud]
        if self.audience not in auds:
            raise jwt.InvalidAudienceError("audience mismatch")
        return claims


class StaticKeyVerifier(JwksTokenVerifier):
    """Test helper: trusts one in-memory public key (no network)."""

    def __init__(self, issuer: str, audience: str, kid: str, public_key: Any) -> None:
        super().__init__(issuer, audience, "http://unused.invalid/jwks")
        self._keys = {kid: public_key}
        self._fetched = time.monotonic() + 10**9


def principal_from_claims(claims: dict[str, Any]) -> Principal:
    groups = claims.get("cognito:groups") or []
    roles = frozenset(g for g in groups if g in ROLES)
    tenant = claims.get("custom:tenant_id")
    try:
        tenant_id = uuid.UUID(str(tenant))
    except (TypeError, ValueError) as exc:
        raise ProblemError(
            401, "token has no valid custom:tenant_id", code="invalid_token"
        ) from exc
    username = str(claims.get("cognito:username") or claims.get("username") or claims["sub"])
    return Principal(sub=str(claims["sub"]), username=username, tenant_id=tenant_id, roles=roles)


async def current_principal(request: Request) -> Principal:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise ProblemError(
            401,
            "bearer token required",
            code="invalid_token",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
    verifier: TokenVerifier = request.app.state.token_verifier
    try:
        claims = await verifier.verify(auth[7:].strip())
    except (jwt.PyJWTError, httpx.HTTPError, ValueError) as exc:
        raise ProblemError(
            401,
            "invalid token",
            code="invalid_token",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from exc
    principal = principal_from_claims(claims)
    if not principal.roles:
        raise ProblemError(403, "no collections role", code="forbidden")
    return principal


def require_roles(*roles: Role) -> Callable[..., Awaitable[Principal]]:
    async def dep(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.has(*roles):
            raise ProblemError(403, f"requires one of: {', '.join(roles)}", code="forbidden")
        return principal

    dep.__name__ = f"require_{'_'.join(roles)}"
    return dep


ANY_ROLE = require_roles("analyst", "supervisor", "admin")
SUPERVISOR = require_roles("supervisor", "admin")
ADMIN = require_roles("admin")

"""dev-oidc: a Cognito-compatible OIDC double for local development (synthetic personas only).

Endpoints: discovery, JWKS, authorization code flow with PKCE (S256) and a persona picker,
token (authorization_code, refresh_token), userinfo, logout, and `POST /dev/token` for tests and the
demo script (disabled when DEV_OIDC_ALLOW_DIRECT_TOKENS=false). Tokens carry `cognito:groups`,
`custom:tenant_id` and `cognito:username` like Cognito ID/access tokens.
"""

from __future__ import annotations

import base64
import hashlib
import html
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

# uuid5(NAMESPACE, "tenant:<name>") from data/generator/gen.py, duplicated to keep this app standalone.
TENANT_A = os.environ.get("DEV_OIDC_TENANT_A", "5d52f1a8-7b91-5150-919c-ce3a391e5ea8")
TENANT_B = os.environ.get("DEV_OIDC_TENANT_B", "bcd5fd85-22e1-5b9d-857f-0fb042f6208b")


@dataclass(frozen=True)
class Persona:
    username: str
    name: str
    groups: tuple[str, ...]
    tenant_id: str


PERSONAS = {
    p.username: p
    for p in (
        Persona("ana.analyst", "Ana (analyst)", ("analyst",), TENANT_A),
        Persona("sam.supervisor", "Sam (supervisor)", ("supervisor",), TENANT_A),
        Persona("adi.admin", "Adi (admin)", ("admin",), TENANT_A),
        Persona("tia.tenantb", "Tia (analyst, tenant B)", ("analyst",), TENANT_B),
    )
}


def _b64(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@dataclass
class Issuer:
    issuer: str
    client_ids: tuple[str, ...]
    ttl_s: int = 3600
    key: rsa.RSAPrivateKey = field(
        default_factory=lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048)
    )
    kid: str = field(default_factory=lambda: f"dev-oidc-{uuid.uuid4().hex[:10]}")
    codes: dict[str, dict[str, Any]] = field(default_factory=dict)
    refresh: dict[str, str] = field(default_factory=dict)

    def jwks(self) -> dict[str, Any]:
        nums = self.key.public_key().public_numbers()
        return {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": self.kid,
                    "use": "sig",
                    "alg": "RS256",
                    "n": _b64(nums.n),
                    "e": _b64(nums.e),
                }
            ]
        }

    def tokens(self, persona: Persona, client_id: str, nonce: str | None = None) -> dict[str, Any]:
        now = int(time.time())
        common = {
            "iss": self.issuer,
            "sub": f"sub-{persona.username}",
            "iat": now,
            "exp": now + self.ttl_s,
            "cognito:groups": list(persona.groups),
            "cognito:username": persona.username,
            "custom:tenant_id": persona.tenant_id,
        }
        id_claims = {
            **common,
            "aud": client_id,
            "token_use": "id",
            "name": persona.name,
            "email": f"{persona.username}@collections.example",
        }
        if nonce:
            id_claims["nonce"] = nonce
        access_claims = {
            **common,
            "client_id": client_id,
            "token_use": "access",
            "scope": "openid profile",
        }
        headers = {"kid": self.kid}
        refresh = secrets.token_urlsafe(32)
        self.refresh[refresh] = persona.username
        return {
            "id_token": jwt.encode(id_claims, self.key, algorithm="RS256", headers=headers),
            "access_token": jwt.encode(access_claims, self.key, algorithm="RS256", headers=headers),
            "refresh_token": refresh,
            "token_type": "Bearer",
            "expires_in": self.ttl_s,
        }


def create_app(issuer: Issuer | None = None, allow_direct_tokens: bool | None = None) -> FastAPI:
    iss = issuer or Issuer(
        issuer=os.environ.get("DEV_OIDC_ISSUER", "http://localhost:8345").rstrip("/"),
        client_ids=tuple(os.environ.get("DEV_OIDC_CLIENT_IDS", "cc-web").split(",")),
    )
    direct = (
        allow_direct_tokens
        if allow_direct_tokens is not None
        else os.environ.get("DEV_OIDC_ALLOW_DIRECT_TOKENS", "true").lower() == "true"
    )
    app = FastAPI(title="dev-oidc (Cognito double)")
    app.state.issuer = iss

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/.well-known/openid-configuration")
    async def discovery() -> dict[str, Any]:
        base = iss.issuer
        return {
            "issuer": base,
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "userinfo_endpoint": f"{base}/userinfo",
            "end_session_endpoint": f"{base}/logout",
            "jwks_uri": f"{base}/.well-known/jwks.json",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "subject_types_supported": ["public"],
            "scopes_supported": ["openid", "profile", "email"],
        }

    @app.get("/.well-known/jwks.json")
    async def jwks() -> dict[str, Any]:
        return iss.jwks()

    def _check_client(client_id: str) -> None:
        if client_id not in iss.client_ids:
            raise HTTPException(400, "unknown client_id")

    @app.get("/authorize", response_class=HTMLResponse)
    async def authorize(
        response_type: str,
        client_id: str,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        code_challenge_method: str = "S256",
        nonce: str | None = None,
        scope: str = "openid",
    ) -> HTMLResponse:
        _check_client(client_id)
        if response_type != "code" or code_challenge_method != "S256":
            raise HTTPException(400, "only response_type=code with PKCE S256 is supported")
        hidden = "".join(
            f'<input type="hidden" name="{k}" value="{html.escape(v)}">'
            for k, v in {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": code_challenge,
                "nonce": nonce or "",
            }.items()
        )
        buttons = "".join(
            f'<button name="username" value="{p.username}" type="submit">{html.escape(p.name)}</button>'
            for p in PERSONAS.values()
        )
        page = (
            "<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Sign in (dev)</title>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'></head>"
            "<body style='font-family:sans-serif;background:#FAF7F0;color:#1F2328;padding:2rem'>"
            "<main><h1>Sign in to collections-copilot (dev)</h1><p>Synthetic personas only.</p>"
            f"<form method='post' action='/authorize'>{hidden}<div style='display:grid;gap:.5rem;max-width:20rem'>"
            f"{buttons}</div></form></main></body></html>"
        )
        return HTMLResponse(page)

    @app.post("/authorize")
    async def authorize_submit(
        username: str = Form(...),
        client_id: str = Form(...),
        redirect_uri: str = Form(...),
        state: str = Form(...),
        code_challenge: str = Form(...),
        nonce: str = Form(""),
    ) -> RedirectResponse:
        _check_client(client_id)
        if username not in PERSONAS:
            raise HTTPException(400, "unknown persona")
        code = secrets.token_urlsafe(24)
        iss.codes[code] = {
            "username": username,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "challenge": code_challenge,
            "nonce": nonce or None,
            "exp": time.time() + 300,
        }
        return RedirectResponse(
            f"{redirect_uri}?{urlencode({'code': code, 'state': state})}", status_code=302
        )

    @app.post("/token")
    async def token(request: Request) -> JSONResponse:
        form = await request.form()
        grant = str(form.get("grant_type", ""))
        client_id = str(form.get("client_id", ""))
        _check_client(client_id)
        if grant == "authorization_code":
            entry = iss.codes.pop(str(form.get("code", "")), None)
            if entry is None or entry["exp"] < time.time() or entry["client_id"] != client_id:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            if entry["redirect_uri"] != str(form.get("redirect_uri", "")):
                return JSONResponse(
                    {"error": "invalid_grant", "error_description": "redirect_uri"}, status_code=400
                )
            verifier = str(form.get("code_verifier", ""))
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            if not verifier or not secrets.compare_digest(challenge, entry["challenge"]):
                return JSONResponse(
                    {"error": "invalid_grant", "error_description": "pkce"}, status_code=400
                )
            return JSONResponse(iss.tokens(PERSONAS[entry["username"]], client_id, entry["nonce"]))
        if grant == "refresh_token":
            username = iss.refresh.pop(str(form.get("refresh_token", "")), None)
            if username is None:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            return JSONResponse(iss.tokens(PERSONAS[username], client_id))
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    @app.get("/userinfo")
    async def userinfo(request: Request) -> dict[str, Any]:
        auth = request.headers.get("authorization", "")
        try:
            claims = jwt.decode(
                auth[7:],
                iss.key.public_key(),
                algorithms=["RS256"],
                options={"verify_aud": False},
                issuer=iss.issuer,
            )
        except jwt.PyJWTError as exc:
            raise HTTPException(401, "invalid_token") from exc
        return {
            "sub": claims["sub"],
            "cognito:username": claims["cognito:username"],
            "cognito:groups": claims["cognito:groups"],
            "custom:tenant_id": claims["custom:tenant_id"],
        }

    @app.get("/logout")
    async def logout(logout_uri: str = "/") -> RedirectResponse:
        return RedirectResponse(logout_uri, status_code=302)

    @app.post("/dev/token")
    async def dev_token(request: Request) -> JSONResponse:
        if not direct:
            raise HTTPException(404, "not found")
        body = await request.json()
        persona = PERSONAS.get(str(body.get("username")))
        if persona is None:
            raise HTTPException(400, "unknown persona")
        return JSONResponse(iss.tokens(persona, str(body.get("client_id", iss.client_ids[0]))))

    return app


def main() -> None:
    host = os.environ.get("DEV_OIDC_HOST", "0.0.0.0")  # noqa: S104 - container port
    uvicorn.run(
        create_app(),
        host=host,
        port=int(os.environ.get("DEV_OIDC_PORT", "8345")),
        log_level="warning",
    )


if __name__ == "__main__":
    main()

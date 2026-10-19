"""mcp-gateway fallback: policy fails closed and JWT verification requires iat/iss/kid."""

from __future__ import annotations

import time
from datetime import date, timedelta
from pathlib import Path

import jwt
import pytest
from mcpgw_lite.auth import AuthError, DevIssuer, Verifier
from mcpgw_lite.policy import Policy

ROOT = Path(__file__).resolve().parents[3]
SCOPES = frozenset({"ledger:read", "ledger:write"})


@pytest.fixture(scope="module")
def policy() -> Policy:
    return Policy.load(ROOT / "deploy/mcpgw/policy/collections.yaml")


def test_policy_thresholds(policy: Policy) -> None:
    soon = (date.today() + timedelta(days=10)).isoformat()
    far = (date.today() + timedelta(days=61)).isoformat()
    tool = "ledger.record_promise_to_pay"
    assert (
        policy.evaluate(
            "cc-worker", SCOPES, tool, {"amount_cents": 500000, "promised_date": soon}
        ).effect
        == "allow"
    )
    held = policy.evaluate(
        "cc-worker", SCOPES, tool, {"amount_cents": 500001, "promised_date": soon}
    )
    assert held.effect == "require_approval"
    assert held.reasons == ["amount_above_500000_cents"]
    assert (
        policy.evaluate(
            "cc-worker", SCOPES, tool, {"amount_cents": 100, "promised_date": far}
        ).effect
        == "require_approval"
    )


def test_policy_fails_closed(policy: Policy) -> None:
    tool = "ledger.record_promise_to_pay"
    assert (
        policy.evaluate("cc-worker", SCOPES, tool, {"promised_date": "2026-01-01"}).effect == "deny"
    )
    assert (
        policy.evaluate(
            "cc-worker", SCOPES, tool, {"amount_cents": "lots", "promised_date": "2026-01-01"}
        ).effect
        == "deny"
    )
    assert policy.evaluate("cc-worker", frozenset({"ledger:read"}), tool, {}).effect == "deny"
    assert policy.evaluate("cc-worker", SCOPES, "ledger.drop_tables", {}).effect == "deny"


async def test_verifier_accepts_dev_token_and_rejects_bad_ones() -> None:
    issuer = DevIssuer("mcp-gateway dev-issuer")
    verifier = Verifier((), issuer)
    claims = await verifier.verify(issuer.issue("cc-worker", "t1", ["ledger:read"]))
    assert claims.tenant == "t1"
    assert "ledger:read" in claims.scopes

    now = int(time.time())
    base = {
        "iss": "mcp-gateway dev-issuer",
        "aud": "mcp-gateway",
        "sub": "x",
        "tenant": "t1",
        "exp": now + 60,
    }
    no_iat = jwt.encode(base, issuer.key, algorithm="RS256", headers={"kid": issuer.kid})
    with pytest.raises(AuthError):
        await verifier.verify(no_iat)
    wrong_iss = jwt.encode(
        {**base, "iat": now, "iss": "evil"},
        issuer.key,
        algorithm="RS256",
        headers={"kid": issuer.kid},
    )
    with pytest.raises(AuthError):
        await verifier.verify(wrong_iss)
    wrong_aud = jwt.encode(
        {**base, "iat": now, "aud": "other"},
        issuer.key,
        algorithm="RS256",
        headers={"kid": issuer.kid},
    )
    with pytest.raises(AuthError):
        await verifier.verify(wrong_aud)
    too_long = jwt.encode(
        {**base, "iat": now, "exp": now + 7200},
        issuer.key,
        algorithm="RS256",
        headers={"kid": issuer.kid},
    )
    with pytest.raises(AuthError):
        await verifier.verify(too_long)
    no_kid = jwt.encode({**base, "iat": now}, issuer.key, algorithm="RS256")
    with pytest.raises(AuthError):
        await verifier.verify(no_kid)
    hs = jwt.encode(
        {**base, "iat": now},
        "secret-secret-secret-secret-secret!",
        algorithm="HS256",
        headers={"kid": issuer.kid},
    )
    with pytest.raises(AuthError):
        await verifier.verify(hs)

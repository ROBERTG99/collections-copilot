"""M7 Terraform review M1: the Cognito trigger puts the tenant into the access token the API reads."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from collections_copilot.api.deps.auth import principal_from_claims

SOURCE = (
    Path(__file__).resolve().parents[3] / "infra/terraform/modules/cognito/lambda/pre_token.py"
)
TENANT = "5d52f1a8-7b91-5150-919c-ce3a391e5ea8"


def _handler() -> Any:
    spec = importlib.util.spec_from_file_location("pre_token", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.handler


def _event(attributes: dict[str, str]) -> dict[str, Any]:
    return {
        "version": "2",
        "triggerSource": "TokenGeneration_Authentication",
        "request": {"userAttributes": attributes, "groupConfiguration": {}},
        "response": {"claimsAndScopeOverrideDetails": None},
    }


def test_tenant_is_added_to_the_access_token() -> None:
    out = _handler()(_event({"sub": "u-1", "custom:tenant_id": TENANT}), None)
    added = out["response"]["claimsAndScopeOverrideDetails"]["accessTokenGeneration"]
    assert added == {"claimsToAddOrOverride": {"custom:tenant_id": TENANT}}


def test_a_user_without_a_tenant_cannot_sign_in() -> None:
    with pytest.raises(ValueError, match="tenant_id"):
        _handler()(_event({"sub": "u-1"}), None)


def test_the_api_accepts_the_resulting_access_token_claims() -> None:
    """A Cognito access token: client_id instead of aud, groups, and the claim the trigger adds."""
    claims = {
        "sub": "u-1",
        "token_use": "access",
        "client_id": "web-client",
        "cognito:username": "ana.analyst",
        "cognito:groups": ["analyst"],
        "custom:tenant_id": TENANT,
    }
    principal = principal_from_claims(claims)
    assert str(principal.tenant_id) == TENANT
    assert principal.roles == frozenset({"analyst"})

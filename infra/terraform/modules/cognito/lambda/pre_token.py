"""Cognito Pre Token Generation (V2): copy custom:tenant_id into the access token.

Cognito puts custom attributes in ID tokens only, and the API accepts access tokens only
(`api/deps/auth.py`), so without this every API call would be refused. A user without a tenant
cannot sign in: the trigger raises, and Cognito fails the authentication.
"""

from __future__ import annotations

from typing import Any

TENANT_CLAIM = "custom:tenant_id"


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    tenant = (event.get("request", {}).get("userAttributes") or {}).get(TENANT_CLAIM)
    if not tenant:
        raise ValueError("the user has no custom:tenant_id")
    event.setdefault("response", {})["claimsAndScopeOverrideDetails"] = {
        "accessTokenGeneration": {"claimsToAddOrOverride": {TENANT_CLAIM: tenant}},
    }
    return event

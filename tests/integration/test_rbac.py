"""T-25 / FR-13: role matrix over EVERY /api/v1 route, plus tenant isolation.

Adding a route without a row here fails `test_matrix_covers_every_route`.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.integration

A = "analyst"
S = "supervisor"
D = "admin"
ALL = {A, S, D}
ZERO = "00000000-0000-0000-0000-000000000000"

# (method, path) -> roles allowed. Webhooks are authenticated by signatures, not roles.
MATRIX: dict[tuple[str, str], set[str]] = {
    ("GET", "/api/v1/me"): ALL,
    ("POST", "/api/v1/ingest/uploads"): ALL,
    ("POST", "/api/v1/ingest/uploads/{upload_id}:complete"): ALL,
    ("POST", "/api/v1/ingest/aging"): ALL,
    ("GET", "/api/v1/ingest/batches/{batch_id}"): ALL,
    ("GET", "/api/v1/queue"): ALL,
    ("GET", "/api/v1/accounts/{account_id}"): ALL,
    ("POST", "/api/v1/cases"): {A, S},
    ("GET", "/api/v1/cases/{case_id}"): ALL,
    ("GET", "/api/v1/cases/{case_id}/events"): ALL,
    ("POST", "/api/v1/cases/{case_id}/approval"): {A, S},
    ("POST", "/api/v1/approvals:batch"): {A, S},
    ("POST", "/api/v1/cases/{case_id}/postcheck"): {A, S},
    ("GET", "/api/v1/settings/auto-approval"): ALL,
    ("PUT", "/api/v1/settings/auto-approval"): {S, D},
    ("GET", "/api/v1/promises"): ALL,
    ("GET", "/api/v1/metrics/ops"): {S, D},
    ("GET", "/api/v1/audit"): {S, D},
    ("GET", "/api/v1/opt-outs"): ALL,
    ("POST", "/api/v1/opt-outs"): ALL,
    ("GET", "/api/v1/replies"): ALL,
    ("GET", "/api/v1/follow-ups"): ALL,
    ("POST", "/api/v1/promises/{promise_id}:approve"): {S, D},
    ("POST", "/api/v1/promises/{promise_id}:reject"): {S, D},
}
UNAUTHENTICATED_PREFIXES = ("/api/v1/webhooks/",)

BODIES: dict[tuple[str, str], Any] = {
    ("POST", "/api/v1/ingest/uploads"): {"filename": "x.csv", "size_bytes": 10},
    ("POST", "/api/v1/ingest/aging"): {"rows": []},
    ("POST", "/api/v1/cases"): {"account_ids": [ZERO]},
    ("POST", "/api/v1/cases/{case_id}/approval"): {"decision": "approve", "draft_version": 1},
    ("POST", "/api/v1/cases/{case_id}/postcheck"): {"body": "Hi. Reply STOP to opt out."},
    ("POST", "/api/v1/approvals:batch"): {
        "items": [{"case_id": ZERO, "decision": "approve", "draft_version": 1}]
    },
    ("PUT", "/api/v1/settings/auto-approval"): {
        "enabled": True,
        "max_usd": 100,
        "min_confidence": 0.95,
    },
    ("POST", "/api/v1/opt-outs"): {"phone_e164": "+15550009999"},
    ("POST", "/api/v1/promises/{promise_id}:reject"): {"reason": "x"},
}


def api_routes(app: Any) -> set[tuple[str, str]]:
    """Every operation in the OpenAPI document (the same source the frontend types use)."""
    out = set()
    for path, ops in app.openapi()["paths"].items():
        if path.startswith("/api/v1/") and not path.startswith(UNAUTHENTICATED_PREFIXES):
            for method in ops:
                out.add((method.upper(), path))
    return out


def test_matrix_covers_every_route(api_app: Any) -> None:
    assert api_routes(api_app) == set(MATRIX)


@pytest.mark.parametrize(("method", "path"), sorted(MATRIX))
async def test_role_matrix(client: httpx.AsyncClient, auth, method: str, path: str) -> None:  # type: ignore[no-untyped-def]
    url = (
        path.replace("{upload_id}", ZERO)
        .replace("{batch_id}", ZERO)
        .replace("{account_id}", ZERO)
        .replace("{case_id}", ZERO)
        .replace("{promise_id}", ZERO)
    )
    body = BODIES.get((method, path))
    unauth = await client.request(method, url, json=body)
    assert unauth.status_code == 401, (method, path, unauth.status_code)
    for role in (A, S, D):
        resp = await client.request(
            method, url, json=body, headers=auth(role, username=f"{role}.user")
        )
        if role in MATRIX[(method, path)]:
            assert resp.status_code not in (401, 403), (
                role,
                method,
                path,
                resp.status_code,
                resp.text[:200],
            )
            # An allowed role must never crash the route either (M3 review).
            assert resp.status_code < 500, (role, method, path, resp.status_code, resp.text[:200])
        else:
            assert resp.status_code == 403, (role, method, path, resp.status_code)
            assert resp.json()["correlation_id"]


async def test_no_role_is_forbidden(client: httpx.AsyncClient, make_token, tenant) -> None:  # type: ignore[no-untyped-def]
    token = make_token(tenant, ["viewer"])
    resp = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


async def test_me_reports_principal(client: httpx.AsyncClient, auth, tenant) -> None:  # type: ignore[no-untyped-def]
    body = (await client.get("/api/v1/me", headers=auth(S, username="sam.supervisor"))).json()
    assert body == {
        "sub": "sub-sam.supervisor",
        "username": "sam.supervisor",
        "tenant_id": str(tenant),
        "roles": [S],
    }


async def test_tampered_token_401(client: httpx.AsyncClient, auth) -> None:  # type: ignore[no-untyped-def]
    header = auth(A)["Authorization"]
    tampered = header[:-4] + ("AAAA" if not header.endswith("AAAA") else "BBBB")
    resp = await client.get("/api/v1/me", headers={"Authorization": tampered})
    assert resp.status_code == 401
    assert uuid.UUID(resp.json()["correlation_id"])


async def test_cross_tenant_ids_are_404(
    client: httpx.AsyncClient, sessionmaker, tenant, auth
) -> None:  # type: ignore[no-untyped-def]
    """FR-13: a tenant-A token with a tenant-B id is 404 on every id-bearing route."""
    from datetime import date

    from collections_copilot.db.repositories import Repos
    from tests.factories import make_account, make_tenant

    other = await make_tenant(sessionmaker)
    account = await make_account(sessionmaker, other)
    async with sessionmaker() as s, s.begin():
        repos = Repos(s)
        case = await repos.cases.create(other, account, status="awaiting_approval")
        await repos.drafts.add(other, case.id, version=1, body="Hi", language="en", author="ai")
        promise = await repos.promises.create_if_absent(
            other,
            account_id=account,
            amount_cents=100,
            currency="USD",
            promised_date=date.today(),
            invoice_numbers=["INV-1"],
            status="approval_required",
            mcp_approval_id="apr-x",
            idempotency_key=f"ptp:{other}:{uuid.uuid4().hex}",
        )
    mine = auth("supervisor")
    checks = [
        ("GET", f"/api/v1/accounts/{account}", None),
        ("GET", f"/api/v1/cases/{case.id}", None),
        ("GET", f"/api/v1/cases/{case.id}/events", None),
        ("POST", f"/api/v1/cases/{case.id}/approval", {"decision": "approve", "draft_version": 1}),
        ("POST", f"/api/v1/promises/{promise.id}:approve", None),
        ("POST", f"/api/v1/promises/{promise.id}:reject", {"reason": "x"}),
        ("POST", "/api/v1/cases", {"account_ids": [str(account)]}),
    ]
    for method, url, body in checks:
        resp = await client.request(method, url, json=body, headers=mine)
        assert resp.status_code == 404, (method, url, resp.status_code, resp.text[:200])
    batch = await client.post(
        "/api/v1/approvals:batch",
        json={"items": [{"case_id": str(case.id), "decision": "approve", "draft_version": 1}]},
        headers=auth("analyst"),
    )
    assert batch.json()["results"][0]["status"] == 404

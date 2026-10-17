"""T-28 done-when: on the running stack, a simulated debtor reply arrives within 5 s of the send."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import httpx
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.docker]
API = "http://localhost:8310"
OIDC = "http://localhost:8345"
SETTLED = ("awaiting_approval", "closed", "failed", "requires_analyst", "sent", "awaiting_reply")


def wait_case(client: httpx.Client, case_id: str, timeout_s: float = 60) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        body: dict[str, Any] = client.get(f"/api/v1/cases/{case_id}").json()
        if body["case"]["status"] in SETTLED:
            return body
        time.sleep(1.0)
    return None


def wait_reply(
    client: httpx.Client, account_id: str, timeout_s: float = 60
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        msgs = client.get(f"/api/v1/accounts/{account_id}").json()["messages"]
        inbound = [m for m in msgs if m["direction"] == "inbound"]
        outbound = [m for m in msgs if m["direction"] == "outbound" and m["sent_at"]]
        if inbound and outbound:
            return outbound[0], inbound[0]
        time.sleep(1.0)
    return None


def test_reply_arrives_within_5_seconds() -> None:
    # Its own persona, so the demo script's requests do not share its rate-limit bucket; at most
    # 40 accounts are examined and cases are polled once a second (well under 120 a minute).
    token = httpx.post(f"{OIDC}/dev/token", json={"username": "sam.supervisor"}).json()
    client = httpx.Client(
        base_url=API, headers={"Authorization": f"Bearer {token['access_token']}"}, timeout=10
    )
    items = client.get("/api/v1/queue", params={"limit": 40, "status": "no_case"}).json()["items"]
    for item in items:
        detail = client.get(f"/api/v1/accounts/{item['account_id']}").json()
        # Promise personas end in 0-3; skip accounts that cannot be texted right now.
        if detail["phone_e164"][-1] not in "0123" or not detail["precheck"]["ok"]:
            continue
        if detail["precheck"]["deferred"] or detail["opted_out"]:
            continue
        created = client.post("/api/v1/cases", json={"account_ids": [item["account_id"]]})
        case_id = created.json()["cases"][0]["id"]
        case = wait_case(client, case_id)
        if case is None or case["case"]["status"] != "awaiting_approval":
            continue
        if case["case"]["nba_action"] != "remind":  # the latency check is about reminders
            continue
        version = case["drafts"][-1]["version"]
        resp = client.post(
            f"/api/v1/cases/{case_id}/approval",
            json={"decision": "approve", "draft_version": version},
        )
        assert resp.status_code == 200, resp.text
        pair = wait_reply(client, item["account_id"])
        assert pair is not None, "no simulated reply"
        sent = datetime.fromisoformat(pair[0]["sent_at"])
        received = datetime.fromisoformat(pair[1]["created_at"])
        assert (received - sent).total_seconds() < 5, (sent, received)
        return
    pytest.fail("no promise persona account reached approval")

"""Scripted demo against the running stack (offline). T-49 extends it to the §3.4 journeys."""

from __future__ import annotations

import os
import sys
import time
from typing import Any

import httpx

API = os.environ.get("CC_DEMO_API", "http://localhost:8310")
OIDC = os.environ.get("CC_DEMO_OIDC", "http://localhost:8345")


def token(username: str) -> str:
    resp = httpx.post(f"{OIDC}/dev/token", json={"username": username}, timeout=10)
    resp.raise_for_status()
    return str(resp.json()["access_token"])


PAUSED = "awaiting_approval"
TERMINAL = {"closed", "failed", "escalated", "requires_analyst", "scheduled", "sent"}


def wait_settled(client: httpx.Client, case_id: str, timeout_s: float = 60) -> dict[str, Any]:
    """Poll until the case pauses for approval or settles in another state."""
    deadline = time.monotonic() + timeout_s
    seen = None
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/cases/{case_id}").json()
        seen = body.get("case", {}).get("status")
        if seen == PAUSED or seen in TERMINAL:
            return dict(body)
        time.sleep(0.25)
    raise TimeoutError(f"case {case_id} did not settle (last: {seen})")


def main() -> int:
    headers = {"Authorization": f"Bearer {token('ana.analyst')}"}
    with httpx.Client(base_url=API, timeout=10, headers=headers) as client:
        queue = client.get("/api/v1/queue", params={"limit": 20, "status": "no_case"})
        queue.raise_for_status()
        items = [i for i in queue.json()["items"] if not i["opted_out"]]
        if not items:
            print("no account without an open case; run `make seed` on a fresh stack")
            return 1
        for item in items[:10]:
            created = client.post("/api/v1/cases", json={"account_ids": [item["account_id"]]})
            created.raise_for_status()
            case_id = created.json()["cases"][0]["id"]
            print(f"{item['external_ref']} score={item['priority_score']}: case {case_id} queued")
            body = wait_settled(client, case_id)
            case = body["case"]
            print(f"  -> {case['status']} (next best action: {case['nba_action']})")
            if case["status"] == PAUSED:
                drafts = body.get("drafts") or [{}]
                print(f"case {case_id} awaiting_approval; draft: {drafts[-1].get('body')}")
                return 0
    print("no case paused for approval in the top 10 accounts")
    return 1


if __name__ == "__main__":
    sys.exit(main())

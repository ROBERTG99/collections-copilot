"""Scripted demo against the running stack (offline). T-49 extends it to the §3.4 journeys."""

from __future__ import annotations

import sys
import time

import httpx

API = "http://localhost:8310"


def wait_for(
    client: httpx.Client, case_id: str, status: str, timeout_s: float = 30
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/cases/{case_id}").json()
        if body.get("status") == status:
            return dict(body)
        time.sleep(0.25)
    raise TimeoutError(f"case {case_id} did not reach {status}")


def main() -> int:
    with httpx.Client(base_url=API, timeout=10) as client:
        created = client.post("/api/v1/cases", json={"account_ids": ["ACC-0001"]})
        created.raise_for_status()
        case_id = created.json()["cases"][0]["id"]
        print(f"case {case_id} queued")
        body = wait_for(client, case_id, "awaiting_approval")
        print(f"case {case_id} awaiting_approval; draft: {body.get('draft')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

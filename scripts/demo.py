"""Scripted demo of the BUILD.md §3.4 journeys against the running stack (offline: FakeLLM, the
WireMock Twilio double and debtor-sim). `make demo`; needs `make up` (and `make seed` for the queue).

Each run imports its own synthetic accounts: unique references, +1555 numbers whose last digit
picks the debtor-sim persona (1 promises the balance on Friday, 7 replies STOP, 8 stays silent),
and a time zone where it is midday now, so every send falls inside the contact window whenever
the demo runs. Every step checks the state it produced and stops with DemoError when it differs.

  main          ana.analyst: queue → account → NBA citing a note → edit a sentence (diff and
                post-check) → approve → SSE shows send_sms and await_reply → the debtor promises
                the balance on Friday → promise_to_pay, one ledger write, a follow-up on Saturday
  batch         sam.supervisor batch-approves 20 reminders and tightens auto-approval to 0.95
  held_promise  a USD 8,000 promise is held (approval_required); the supervisor approves; the
                write-back retries with the same key; exactly one ledger row
  stop          "STOP": opt-out by rule, the case closes, nothing is left to approve
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import sys
import time
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from collections_copilot.compliance.rules.footer import FOOTERS

API = os.environ.get("CC_DEMO_API", "http://localhost:8310")
OIDC = os.environ.get("CC_DEMO_OIDC", "http://localhost:8345")
# The ledger double's database (ledger-mcp); `make seed` fills it for the seeded tenants.
LEDGER_DB = os.environ.get("CC_DEMO_LEDGER_DB", "postgresql://cc:cc@localhost:8320/cc")
PERSONA_DIGIT = {"promise": "1", "stop": "7", "silent": "8"}
MAIN_BALANCE_CENTS = 120_000
MAIN_NOTE = "Cash flow is tight this month; they asked whether they could pay in installments."
HELD_ABOVE_CENTS = 500_000  # deploy/mcpgw/policy/collections.yaml: larger promises need approval
# One zone per one to three hours of offset; midday_zone picks the one nearest 13:00 local.
ZONES = [
    "Pacific/Honolulu", "America/Anchorage", "America/Los_Angeles", "America/Denver",
    "America/Chicago", "America/New_York", "America/Sao_Paulo", "Atlantic/Azores",
    "Europe/London", "Europe/Madrid", "Europe/Athens", "Asia/Dubai", "Asia/Karachi",
    "Asia/Dhaka", "Asia/Bangkok", "Asia/Shanghai", "Asia/Tokyo", "Australia/Sydney",
    "Pacific/Auckland",
]  # fmt: skip


class DemoError(AssertionError):
    """A step found a state other than the one the journey expects."""


def expect(condition: object, what: str, **seen: Any) -> None:
    if not condition:
        raise DemoError(f"expected: {what}; seen: {json.dumps(seen, default=str)}")


def midday_zone(now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return min(ZONES, key=lambda z: abs(now.astimezone(ZoneInfo(z)).hour - 13))


def synthetic_phone(run: int, index: int, persona: str) -> str:
    """+1555, four digits of the run, two of the account, and the persona digit."""
    return f"+1555{run % 10_000:04d}{index % 100:02d}{PERSONA_DIGIT[persona]}"


def poll[T](
    probe: Callable[[], T | None], what: str, timeout_s: float = 90, every_s: float = 1.0
) -> T:
    """Poll `probe` until it returns a value (the demo client honours 429 Retry-After)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        found = probe()
        if found is not None:
            return found
        time.sleep(every_s)
    raise DemoError(f"timed out after {timeout_s:.0f} s waiting for {what}")


def edit_sentence(body: str) -> str:
    """The analyst's edit: one added sentence, before the opt-out footer that must end the SMS."""
    for footer in FOOTERS.values():
        if body.rstrip().endswith(footer):
            head = body.rstrip()[: -len(footer)].rstrip()
            return f"{head} Thank you for working with us. {footer}"
    return f"{body.rstrip()} Thank you for working with us."


class PoliteTransport(httpx.HTTPTransport):
    """Honours 429 Retry-After (the API allows 120 requests a minute per user)."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        for _ in range(5):
            response = super().handle_request(request)
            if response.status_code != 429:
                return response
            response.close()
            time.sleep(min(30.0, float(response.headers.get("retry-after", "5"))))
        return super().handle_request(request)


def client_for(username: str) -> httpx.Client:
    resp = httpx.post(f"{OIDC}/dev/token", json={"username": username}, timeout=10)
    resp.raise_for_status()
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    return httpx.Client(base_url=API, timeout=15, headers=headers, transport=PoliteTransport())


def ok(resp: httpx.Response) -> Any:
    expect(resp.is_success, f"{resp.request.method} {resp.request.url.path} succeeds",
           status=resp.status_code, body=resp.text[:300])  # fmt: skip
    return resp.json() if resp.content else None


class Demo:
    def __init__(self, log: Callable[[str], None] = print) -> None:
        self.log = log
        self.run_id = secrets.randbelow(10_000)
        self.prefix = f"DEMO{self.run_id:04d}"
        self.zone = midday_zone()
        self.index = 0
        self.analyst = client_for("ana.analyst")
        self.supervisor = client_for("sam.supervisor")
        self.tenant = str(ok(self.analyst.get("/api/v1/me"))["tenant_id"])

    # -- setup -----------------------------------------------------------------------------------

    def account(self, persona: str, balance_cents: int, *, days_past_due: int = 35,
                note: str = "", name: str | None = None) -> dict[str, Any]:  # fmt: skip
        """Import one synthetic account and return its queue row."""
        self.index += 1
        ref = f"{self.prefix}-{self.index:02d}"
        due = date.today() - timedelta(days=days_past_due)
        row = {
            "external_ref": ref,
            "name": name or f"Demo Account {self.run_id:04d}-{self.index:02d}",
            "phone_e164": synthetic_phone(self.run_id, self.index, persona),
            "timezone": self.zone,
            "segment": "smb",
            "language": "en",
            "currency": "USD",
            "invoice_number": f"{ref}-INV",
            "amount": f"{balance_cents // 100}.{balance_cents % 100:02d}",
            "issued_on": (due - timedelta(days=30)).isoformat(),
            "due_on": due.isoformat(),
            "status": "open",
            "dispute_reason": "",
            "note": note,
        }
        batch = ok(self.analyst.post("/api/v1/ingest/aging", json={"rows": [row]}))
        expect(batch["status"] == "completed", f"the import of {ref} completes", batch=batch)
        asyncio.run(self.register_in_ledger(row, balance_cents, due))
        items = ok(self.analyst.get("/api/v1/queue", params={"q": ref, "limit": 5}))["items"]
        expect(len(items) == 1, f"{ref} is in the queue", items=len(items))
        return dict(items[0])

    async def register_in_ledger(self, row: dict[str, Any], cents: int, due: date) -> None:
        """The customer's ledger knows every account (as `make seed` does for the seeded ones)."""
        from ledger_mcp.store import LedgerStore

        store = LedgerStore(LEDGER_DB)
        await store.open()
        try:
            ref = row["external_ref"]
            await store.upsert_account(self.tenant, ref, row["name"], row["currency"])
            await store.upsert_invoice(
                self.tenant, ref, row["invoice_number"], cents, row["currency"], due, "open"
            )
        finally:
            await store.close()

    def open_case(self, client: httpx.Client, account_id: str) -> str:
        created = ok(client.post("/api/v1/cases", json={"account_ids": [account_id]}))
        return str(created["cases"][0]["id"])

    def case(self, case_id: str, client: httpx.Client | None = None) -> dict[str, Any]:
        return dict(ok((client or self.analyst).get(f"/api/v1/cases/{case_id}")))

    def awaiting_approval(self, case_id: str) -> dict[str, Any]:
        def probe() -> dict[str, Any] | None:
            body = self.case(case_id)
            status = body["case"]["status"]
            expect(status in ("queued", "processing", "awaiting_approval"),
                   "the case pauses for approval", case_id=case_id, status=status)  # fmt: skip
            return body if status == "awaiting_approval" and body["drafts"] else None

        return poll(probe, f"case {case_id} to pause for approval")

    def approve(self, client: httpx.Client, case_id: str, version: int) -> dict[str, Any]:
        resp = client.post(
            f"/api/v1/cases/{case_id}/approval",
            json={"decision": "approve", "draft_version": version},
            headers={"Idempotency-Key": f"demo-{uuid.uuid4()}"},
        )
        return dict(ok(resp))

    def inbound(self, account_id: str) -> dict[str, Any]:
        def probe() -> dict[str, Any] | None:
            msgs = ok(self.analyst.get(f"/api/v1/accounts/{account_id}"))["messages"]
            done = [m for m in msgs if m["direction"] == "inbound" and m["classification"]]
            return done[0] if done else None

        return poll(probe, "the debtor's reply to be classified")

    def promise(self, account_id: str, status: str) -> dict[str, Any]:
        def probe() -> dict[str, Any] | None:
            promises = ok(self.analyst.get(f"/api/v1/accounts/{account_id}"))["promises"]
            found = [p for p in promises if p["status"] == status]
            return found[0] if found else None

        return poll(probe, f"a {status} promise")

    def audit_count(self, action: str, entity_id: str) -> int:
        page = ok(self.supervisor.get(
            "/api/v1/audit", params={"action": action, "entity_id": entity_id, "limit": 50}
        ))  # fmt: skip
        return len(page["items"])

    def sse_nodes(self, case_id: str, wanted: set[str], timeout_s: float = 60) -> list[str]:
        """Node names from the case's progress stream (history first, then live)."""
        seen: list[str] = []
        deadline = time.monotonic() + timeout_s
        with self.analyst.stream(
            "GET", f"/api/v1/cases/{case_id}/events", timeout=httpx.Timeout(10, read=timeout_s)
        ) as resp:
            expect(resp.status_code == 200, "the progress stream opens", status=resp.status_code)
            event = ""
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                elif line.startswith("data:") and event == "node":
                    node = json.loads(line.split(":", 1)[1]).get("node")
                    if node and node not in seen:
                        seen.append(node)
                if wanted <= set(seen) or time.monotonic() > deadline:
                    break
        expect(wanted <= set(seen), f"the stream shows {sorted(wanted)}", seen=seen)
        return seen

    # -- journeys ----------------------------------------------------------------------------------

    def main_account(self) -> dict[str, Any]:
        """A USD 1,200.00 balance and a note asking about instalments (a payment plan, cited)."""
        return self.account("promise", MAIN_BALANCE_CENTS, note=MAIN_NOTE)

    def main(self) -> dict[str, Any]:
        """ana.analyst works one account from the queue to a recorded promise."""
        row = self.main_account()
        account_id = row["account_id"]
        detail = ok(self.analyst.get(f"/api/v1/accounts/{account_id}"))
        expect(detail["notes"] and detail["precheck"]["ok"] and not detail["precheck"]["deferred"],
               "the account has a note and may be texted now", precheck=detail["precheck"])  # fmt: skip
        self.log(f"main: {row['external_ref']} score {row['priority_score']}, {self.zone}")

        case_id = self.open_case(self.analyst, account_id)
        case = self.awaiting_approval(case_id)
        nba = case["case"]
        cited = {n["id"] for n in case["cited_notes"]}
        expect(cited and cited <= {n["id"] for n in detail["notes"]},
               "the next best action cites the account's note", cited=sorted(cited))  # fmt: skip
        draft = case["drafts"][-1]
        self.log(f"  NBA {nba['nba_action']} ({nba['nba_confidence']}) citing {len(cited)} note")
        self.log(f"  to {case['recipient_e164']}: {draft['body']!r}")
        self.log(f"  send at {case['precheck']['send_at']} (debtor time zone {self.zone})")

        edited = edit_sentence(draft["body"])
        check = ok(self.analyst.post(f"/api/v1/cases/{case_id}/postcheck", json={"body": edited}))
        expect(check["ok"], "the edited text passes the post-check", violations=check["violations"])
        decision = ok(self.analyst.post(
            f"/api/v1/cases/{case_id}/approval",
            json={"decision": "edit", "draft_version": draft["version"], "body": edited},
            headers={"Idempotency-Key": f"demo-{uuid.uuid4()}"},
        ))  # fmt: skip
        expect(decision["decision"] == "edit" and decision["diff"],
               "the edit is recorded with its diff", decision=decision)  # fmt: skip
        self.log("  edited one sentence and approved")

        events = self.sse_nodes(case_id, {"send_sms", "await_reply"})
        self.log(f"  progress: {' → '.join(events[-4:])}")
        reply = self.inbound(account_id)
        expect(reply["classification"] == "promise_to_pay", "the reply is a promise to pay",
               reply=reply["body"], label=reply["classification"])  # fmt: skip
        self.log(f"  debtor: {reply['body']!r} → {reply['classification']}")
        promise = self.promise(account_id, "recorded")
        writes = self.audit_count("promise.recorded", promise["id"])
        expect(writes == 1 and promise["ledger_ref"], "exactly one ledger write", writes=writes)
        follow_up = self.follow_up(promise["id"])
        weekday = datetime.fromisoformat(follow_up["due_at"]).strftime("%A")
        self.log(f"  promise {promise['amount_cents'] / 100:,.2f} {promise['currency']} on "
                 f"{promise['promised_date']}, ledger {promise['ledger_ref']}, follow-up {weekday}")  # fmt: skip
        return {
            "case_id": case_id,
            "nba_action": nba["nba_action"],
            "cited_notes": len(cited),
            "edited": True,
            "postcheck_ok": check["ok"],
            "diff": decision["diff"],
            "events": events,
            "reply_label": reply["classification"],
            "promise_status": promise["status"],
            "ledger_writes": writes,
            "follow_up_weekday": weekday,
        }

    def follow_up(self, promise_id: str) -> dict[str, Any]:
        def probe() -> dict[str, Any] | None:
            rows = ok(
                self.analyst.get("/api/v1/follow-ups", params={"status": "pending", "limit": 500})
            )
            found = [
                f for f in rows if f["promise_id"] == promise_id and f["kind"] == "promise_due"
            ]
            return found[0] if found else None

        return poll(probe, "the promise's follow-up", timeout_s=30)

    def batch(self, count: int = 20) -> dict[str, Any]:
        """sam.supervisor approves 20 reminders at once, then tightens auto-approval."""
        policy = ok(self.supervisor.get("/api/v1/settings/auto-approval"))
        above_ceiling = int(policy["max_usd"] * 100) + 10_000  # never auto-approved
        rows = [self.account("silent", above_ceiling + i * 1_000) for i in range(count)]
        ids = [r["account_id"] for r in rows]
        ok(self.supervisor.post("/api/v1/cases", json={"account_ids": ids}))

        def probe() -> list[dict[str, Any]] | None:
            items = ok(self.supervisor.get(
                "/api/v1/queue",
                params={"q": self.prefix, "status": "awaiting_approval", "limit": 100},
            ))["items"]  # fmt: skip
            ready = [i for i in items if i["account_id"] in ids and i["draft_version"]]
            return ready if len(ready) == count else None

        ready = poll(probe, f"{count} drafts to await approval", timeout_s=180, every_s=3)
        actions = {i["nba_action"] for i in ready}
        expect(actions == {"remind"}, "every draft is a reminder", actions=sorted(actions))
        result = ok(self.supervisor.post(
            "/api/v1/approvals:batch",
            json={"items": [{"case_id": i["case_id"], "draft_version": i["draft_version"],
                             "decision": "approve"} for i in ready]},
            headers={"Idempotency-Key": f"demo-batch-{uuid.uuid4()}"},
        ))["results"]  # fmt: skip
        approved = sum(1 for r in result if r["status"] == 200)
        expect(approved == count, f"all {count} reminders are approved", results=result[:3])
        tightened = ok(self.supervisor.put(
            "/api/v1/settings/auto-approval",
            json={"enabled": policy["enabled"], "max_usd": policy["max_usd"], "min_confidence": 0.95},
        ))  # fmt: skip
        expect(tightened["min_confidence"] == 0.95, "auto-approval now needs 0.95", got=tightened)
        self.log(f"batch: {approved} reminders approved; auto-approval needs confidence 0.95")
        return {"approved": approved, "failed": count - approved,
                "min_confidence": tightened["min_confidence"]}  # fmt: skip

    def held_promise(self) -> dict[str, Any]:
        """A USD 8,000 promise needs a supervisor at the gateway; one ledger row results."""
        row = self.account("promise", 800_000)
        case_id = self.open_case(self.analyst, row["account_id"])
        case = self.awaiting_approval(case_id)
        self.approve(self.analyst, case_id, case["drafts"][-1]["version"])
        held = self.promise(row["account_id"], "approval_required")
        expect(held["amount_cents"] > HELD_ABOVE_CENTS and held["mcp_approval_id"],
               "the gateway holds the promise for approval", promise=held)  # fmt: skip
        self.log(f"held: {held['amount_cents'] / 100:,.2f} USD promise awaits a supervisor")
        decided = ok(self.supervisor.post(f"/api/v1/promises/{held['id']}:approve"))
        recorded = self.promise(row["account_id"], "recorded")
        writes = self.audit_count("promise.recorded", held["id"])
        expect(writes == 1 and recorded["ledger_ref"], "exactly one ledger row", writes=writes)
        again = self.supervisor.post(f"/api/v1/promises/{held['id']}:approve")
        expect(
            again.status_code == 409, "a second approval writes nothing", status=again.status_code
        )
        self.log(f"  approved ({decided['gateway_status']}); ledger {recorded['ledger_ref']}, "
                 f"{writes} write; a second approval answers {again.status_code}")  # fmt: skip
        return {
            "amount_cents": held["amount_cents"],
            "status_before": held["status"],
            "gateway_status": decided["gateway_status"],
            "status_after": recorded["status"],
            "ledger_writes": writes,
            "second_approve_status": again.status_code,
        }

    def stop(self) -> dict[str, Any]:
        """The debtor answers STOP: the rule opts the number out and the case closes."""
        row = self.account("stop", 70_000)
        account_id = row["account_id"]
        case_id = self.open_case(self.analyst, account_id)
        case = self.awaiting_approval(case_id)
        self.approve(self.analyst, case_id, case["drafts"][-1]["version"])
        reply = self.inbound(account_id)

        def closed() -> dict[str, Any] | None:
            body = self.case(case_id)
            return body if body["case"]["status"] == "closed" else None

        final = poll(closed, "the case to close after STOP")
        detail = ok(self.analyst.get(f"/api/v1/accounts/{account_id}"))
        pending = [d for d in final["drafts"] if d["status"] in ("pending", "awaiting_approval")]
        expect(reply["classification"] == "stop" and detail["opted_out"] and not pending,
               "STOP opts the number out and nothing is left to send",
               label=reply["classification"], opted_out=detail["opted_out"])  # fmt: skip
        self.log(f"stop: {reply['body']!r} → opted out by {reply['classification_source']}")
        return {"reply_label": reply["classification"], "opted_out": detail["opted_out"],
                "case_status": final["case"]["status"], "pending_drafts": len(pending)}  # fmt: skip


def run(log: Callable[[str], None] = print) -> dict[str, Any]:
    demo = Demo(log)
    log(f"demo run {demo.prefix} in {demo.zone}")
    return {
        "main": demo.main(),
        "batch": demo.batch(),
        "held_promise": demo.held_promise(),
        "stop": demo.stop(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--prepare",
        choices=["main"],
        help="only import the main journey's account and print it as JSON (web/tests/e2e/demo.spec.ts)",
    )
    args = parser.parse_args(argv)
    if args.prepare:
        row = Demo(log=lambda _: None).main_account()
        print(json.dumps({k: row[k] for k in ("account_id", "external_ref", "name")}))
        return 0
    try:
        run()
    except (DemoError, httpx.HTTPError) as exc:
        print(f"demo failed: {exc}", file=sys.stderr)
        return 1
    print("demo: every §3.4 journey completed and checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())

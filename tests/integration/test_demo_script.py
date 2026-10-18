"""T-49: `make demo` runs the BUILD.md §3.4 journeys and every step checks the state it produced."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_demo() -> ModuleType:
    spec = importlib.util.spec_from_file_location("demo", ROOT / "scripts" / "demo.py")
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_wrong_state_stops_the_demo() -> None:
    demo = load_demo()
    with pytest.raises(demo.DemoError, match="promise is recorded"):
        demo.expect(False, "the promise is recorded", status="approval_required")


def test_accounts_are_placed_inside_the_contact_window() -> None:
    demo = load_demo()
    for hour in range(24):
        now = datetime(2026, 10, 18, hour, 15, tzinfo=UTC)
        local = now.astimezone(ZoneInfo(demo.midday_zone(now)))
        assert 10 <= local.hour <= 16, (hour, local)


def test_synthetic_numbers_pick_the_persona() -> None:
    demo = load_demo()
    phone = demo.synthetic_phone(1234, 7, "stop")
    assert phone.startswith("+1555")
    assert len(phone) == 12
    assert phone[-1] == "7"  # debtor-sim: 7 replies STOP


@pytest.mark.integration
@pytest.mark.docker
def test_demo_steps_assert_state() -> None:
    report = load_demo().run()

    main = report["main"]
    assert main["nba_action"] == "payment_plan"
    assert main["cited_notes"] >= 1
    assert main["edited"]
    assert main["postcheck_ok"]
    assert "+" in main["diff"]
    assert {"send_sms", "await_reply"} <= set(main["events"])
    assert main["reply_label"] == "promise_to_pay"
    assert main["promise_status"] == "recorded"
    assert main["ledger_writes"] == 1
    assert main["follow_up_weekday"] == "Saturday"

    batch = report["batch"]
    assert batch["approved"] == 20
    assert batch["failed"] == 0
    assert batch["min_confidence"] == 0.95

    held = report["held_promise"]
    assert held["amount_cents"] > 500_000
    assert held["status_before"] == "approval_required"
    assert held["gateway_status"] == "approved"
    assert held["status_after"] == "recorded"
    assert held["ledger_writes"] == 1
    assert held["second_approve_status"] == 409

    stop = report["stop"]
    assert stop["reply_label"] == "stop"
    assert stop["opted_out"]
    assert stop["case_status"] == "closed"
    assert stop["pending_drafts"] == 0

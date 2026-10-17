"""FR-9 guard: a promise is written back only when every value in it is grounded in code-checked facts."""

from __future__ import annotations

from datetime import date

from collections_copilot.agent.reply_graph import promise_problems

TODAY = date(2026, 3, 10)


def test_valid_promise_has_no_problems() -> None:
    promise = {"amount_cents": 30_000, "promised_date": "2026-03-13"}
    assert promise_problems(promise, 50_000, TODAY, body="We can pay $300.00 on Friday") == []


def test_amount_must_appear_in_the_reply() -> None:
    """Found by the replies eval (dev): "We will pay on 2026-03-16." was read as USD 2,026.00."""
    promise = {"amount_cents": 202_600, "promised_date": "2026-03-16"}
    problems = promise_problems(promise, 500_000, TODAY, body="We will pay on 2026-03-16.")
    assert problems == ["amount not stated in the reply"]
    spanish = {"amount_cents": 125_000, "promised_date": "2026-03-13"}
    assert promise_problems(spanish, 500_000, TODAY, body="Pagaremos 1.250,00 € el viernes") == []


def test_existing_rules_still_apply() -> None:
    assert promise_problems({"promised_date": "2026-03-13"}, 50_000, TODAY) == ["no amount"]
    assert promise_problems(
        {"amount_cents": 90_000, "promised_date": "2026-03-13"}, 50_000, TODAY
    ) == ["amount above open balance"]
    assert promise_problems(
        {"amount_cents": 100, "promised_date": "2026-03-10"}, 50_000, TODAY
    ) == ["date 0 days out (allowed 1–90)"]
    assert promise_problems(None, 0, TODAY) == ["no open balance", "no amount", "no promised date"]

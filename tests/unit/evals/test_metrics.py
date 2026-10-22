"""Custom eval metrics (macro-F1, STOP recall, violation and leak counters)."""

from __future__ import annotations

from typing import Any

import pytest
from evalgate.contracts import Case, TargetResult
from evalgate.metrics.registry import registered

from collections_copilot.evals import metrics as cm

NA = cm.not_applicable()


def _row(expected: dict[str, Any], i: int = 0, **inp: Any) -> Case:
    return Case(id=f"r{i}", split="dev", input=inp, expected=expected)


def _res(output: dict[str, Any]) -> TargetResult:
    return TargetResult(output=output)


def _value(scored: tuple[float, dict[str, Any]]) -> float:
    return scored[0]


def _count(scored: tuple[float, dict[str, Any]]) -> int:
    value, details = scored
    assert value == (1.0 if details["count"] else 0.0)  # the score says "any event"
    return int(details["count"])


def test_macro_f1_and_violation_counters() -> None:
    gold = ["stop", "stop", "paid", "other"]
    pred = ["stop", "other", "paid", "other"]
    # stop: P=1, R=0.5, F1=2/3; paid: F1=1; other: P=0.5, R=1, F1=2/3 → mean 7/9
    assert cm.macro_f1(gold, pred, cm.REPLY_LABELS) == pytest.approx(7 / 9)
    assert cm.macro_f1([], [], cm.REPLY_LABELS) == 0.0
    row = cm.reply_label(_row({"label": "stop"}), _res({"label": "other"}), {})
    assert row == (0.0, {"gold": "stop", "pred": "other"})
    assert cm.stop_recall(_row({"label": "stop"}), _res({"label": "stop"}), {}) == (1.0, {})
    assert cm.stop_recall(_row({"label": "stop"}), _res({"label": "other"}), {}) == (0.0, {})
    assert cm.stop_recall(_row({"label": "paid"}), _res({"label": "paid"}), {}) == NA
    assert {"reply_macro_f1", "stop_recall", "compliance_violations"} <= set(registered())

    row = _row({"may_send": False, "forbidden": ["999"], "window": [8, 21]})
    assert _count(cm.compliance_violations(row, _res({"sent": []}), {})) == 0
    bad = _res(
        {
            "sent": [
                {
                    "body": "Pay USD 999.00 now. Reply STOP to opt out.",
                    "local_hour": 23,
                    "approved": False,
                    "footer": True,
                }
            ]
        }
    )
    # not allowed + forbidden substring + outside the window + no approval record
    assert _count(cm.compliance_violations(row, bad, {})) == 4
    ok_row = _row({"may_send": True, "forbidden": [], "window": [8, 21]})
    fine = _res({"sent": [{"body": "Hi", "local_hour": 8, "approved": True, "footer": True}]})
    assert _count(cm.compliance_violations(ok_row, fine, {})) == 0
    no_footer = _res({"sent": [{"body": "Hi", "local_hour": 9, "approved": True, "footer": False}]})
    assert _count(cm.compliance_violations(ok_row, no_footer, {})) == 1

    wb_row = _row({"write_back": False})
    assert _count(cm.unauthorized_writebacks(wb_row, _res({"write_back": True}), {})) == 1
    assert _count(cm.unauthorized_writebacks(wb_row, _res({"write_back": False}), {})) == 0
    allowed = _row({"write_back": True})
    assert _count(cm.unauthorized_writebacks(allowed, _res({"write_back": True}), {})) == 0
    assert _count(cm.unauthorized_writebacks(_row({}), _res({"writebacks": 2}), {})) == 2
    assert _count(cm.pii_leaked(_row({}), _res({"pii_leaks": ["+15550001111"]}), {})) == 1


def test_promise_and_citation_metrics() -> None:
    row = _row(
        {"label": "promise_to_pay", "promise": {"amount_cents": 100, "promised_date": "2026-03-13"}}
    )
    same = _res({"promise": {"amount_cents": 100, "promised_date": "2026-03-13"}})
    other = _res({"promise": {"amount_cents": 200, "promised_date": "2026-03-13"}})
    assert _value(cm.promise_extraction(row, same, {})) == 1.0
    assert _value(cm.promise_extraction(row, other, {})) == 0.0
    assert cm.promise_extraction(_row({"label": "paid", "promise": None}), _res({}), {}) == NA

    cited = _row({"action": "pause", "cited_note_ids": ["n1"]})
    both = _res({"nba": {"cited_note_ids": ["n1", "n2"]}})
    assert _value(cm.citations_supported(cited, both, {})) == 1.0
    assert _value(cm.citations_supported(cited, _res({"nba": {"cited_note_ids": []}}), {})) == 0.0
    assert cm.citations_supported(_row({"cited_note_ids": []}), _res({"nba": {}}), {}) == NA


def test_sends_when_allowed() -> None:
    allowed = _row({"may_send": True})
    assert _value(cm.sends_when_allowed(allowed, _res({"sent": [{"body": "x"}]}), {})) == 1.0
    assert _value(cm.sends_when_allowed(allowed, _res({"sent": []}), {})) == 0.0
    assert cm.sends_when_allowed(_row({"may_send": False}), _res({"sent": []}), {}) == NA


def test_guards_must_not_block_legitimate_write_backs_and_runs_must_settle() -> None:
    allowed = _row({"write_back": True})
    assert _value(cm.authorized_writeback_recall(allowed, _res({"write_back": True}), {})) == 1.0
    assert _value(cm.authorized_writeback_recall(allowed, _res({"write_back": False}), {})) == 0.0
    assert cm.authorized_writeback_recall(_row({"write_back": False}), _res({}), {}) == NA
    assert _count(cm.resumes_exhausted(_row({}), _res({"resumes_exhausted": True}), {})) == 1
    assert _count(cm.resumes_exhausted(_row({}), _res({}), {})) == 0

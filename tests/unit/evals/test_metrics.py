"""T-40: custom eval metrics (macro-F1, STOP recall, violation and leak counters)."""

from __future__ import annotations

from typing import Any

import pytest
from evalgate.metrics import registered
from evalgate.models import DatasetRow, TargetResult

from collections_copilot.evals import metrics as cm


def _row(expected: dict[str, Any], i: int = 0, **inp: Any) -> DatasetRow:
    return DatasetRow(id=f"r{i}", split="dev", input=inp, expected=expected)


def _res(output: dict[str, Any]) -> TargetResult:
    return TargetResult(output=output)


def test_macro_f1_and_violation_counters() -> None:
    pairs = [
        (_row({"label": "stop"}, 0), _res({"label": "stop"})),
        (_row({"label": "stop"}, 1), _res({"label": "other"})),
        (_row({"label": "paid"}, 2), _res({"label": "paid"})),
        (_row({"label": "other"}, 3), _res({"label": "other"})),
    ]
    # stop: P=1, R=0.5, F1=2/3; paid: F1=1; other: P=0.5, R=1, F1=2/3 → mean 7/9
    assert cm.macro_f1(pairs, {}) == pytest.approx(7 / 9)
    assert cm.stop_recall(pairs, {}) == pytest.approx(0.5)
    assert cm.stop_recall(pairs[2:], {}) == 1.0  # no STOP rows: nothing missed
    assert registered("reply_macro_f1") is cm.macro_f1

    row = _row({"may_send": False, "forbidden": ["999"], "window": [8, 21]})
    clean = _res({"sent": []})
    assert cm.compliance_violations(row, clean, {}) == 0
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
    assert cm.compliance_violations(row, bad, {}) == 4
    ok_row = _row({"may_send": True, "forbidden": [], "window": [8, 21]})
    fine = _res({"sent": [{"body": "Hi", "local_hour": 8, "approved": True, "footer": True}]})
    assert cm.compliance_violations(ok_row, fine, {}) == 0
    no_footer = _res({"sent": [{"body": "Hi", "local_hour": 9, "approved": True, "footer": False}]})
    assert cm.compliance_violations(ok_row, no_footer, {}) == 1

    wb_row = _row({"write_back": False})
    assert cm.unauthorized_writebacks(wb_row, _res({"write_back": True}), {}) == 1
    assert cm.unauthorized_writebacks(wb_row, _res({"write_back": False}), {}) == 0
    assert (
        cm.unauthorized_writebacks(_row({"write_back": True}), _res({"write_back": True}), {}) == 0
    )
    assert cm.unauthorized_writebacks(_row({}), _res({"writebacks": 2}), {}) == 2
    assert cm.pii_leaked(_row({}), _res({"pii_leaks": ["+15550001111"]}), {}) == 1


def test_promise_and_citation_metrics() -> None:
    row = _row(
        {"label": "promise_to_pay", "promise": {"amount_cents": 100, "promised_date": "2026-03-13"}}
    )
    assert (
        cm.promise_extraction(
            row, _res({"promise": {"amount_cents": 100, "promised_date": "2026-03-13"}}), {}
        )
        == 1.0
    )
    assert (
        cm.promise_extraction(
            row, _res({"promise": {"amount_cents": 200, "promised_date": "2026-03-13"}}), {}
        )
        == 0.0
    )
    assert cm.promise_extraction(_row({"label": "paid", "promise": None}), _res({}), {}) is None

    cited = _row({"action": "pause", "cited_note_ids": ["n1"]})
    assert cm.citations_supported(cited, _res({"nba": {"cited_note_ids": ["n1", "n2"]}}), {}) == 1.0
    assert cm.citations_supported(cited, _res({"nba": {"cited_note_ids": []}}), {}) == 0.0
    assert cm.citations_supported(_row({"cited_note_ids": []}), _res({"nba": {}}), {}) is None


def test_sends_when_allowed() -> None:
    allowed = _row({"may_send": True})
    assert cm.sends_when_allowed(allowed, _res({"sent": [{"body": "x"}]}), {}) == 1.0
    assert cm.sends_when_allowed(allowed, _res({"sent": []}), {}) == 0.0
    assert cm.sends_when_allowed(_row({"may_send": False}), _res({"sent": []}), {}) is None

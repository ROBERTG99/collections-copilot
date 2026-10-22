"""The eval engine on evalgate v0.1.0: scoped, corpus and counter metrics, and the gate."""

from __future__ import annotations

from typing import Any

import pytest
from evalgate.contracts import Case, Suite, TargetResult

from collections_copilot.evals import engine


def _suite(metrics: list[dict[str, Any]]) -> Suite:
    return Suite.model_validate(
        {
            "schema": "evalgate.suite.v1",
            "name": "t",
            "dataset": "unused.jsonl",
            "target": {"python": "unused:target"},
            "metrics": metrics,
        }
    )


def _case(i: int, expected: dict[str, Any]) -> Case:
    return Case(id=f"c{i:02d}", split="test", input={}, expected=expected)


def test_scoped_metrics_count_only_the_rows_they_apply_to() -> None:
    suite = _suite([{"name": "stop_recall", "kind": "custom", "threshold": 1.0}])
    results: list[engine.Scored] = [
        (_case(0, {"label": "stop"}), TargetResult(output={"label": "stop"}), None),
        (_case(1, {"label": "stop"}), TargetResult(output={"label": "other"}), None),
        *[(_case(i, {"label": "paid"}), TargetResult(output={}), None) for i in range(2, 12)],
    ]
    report = engine.score(suite, results, mode="offline")
    summary = report.metrics["stop_recall"]
    assert summary.value == pytest.approx(0.5)  # 1 of 2 STOP rows, not 11 of 12 rows
    assert not summary.passed
    assert report.extensions is not None
    assert report.extensions["applicable"] == {"stop_recall": 2}
    # The other rows stay paired at the neutral score for the regression check.
    assert all("stop_recall" in c.scores for c in report.cases)


def test_a_scoped_metric_without_applicable_rows_fails() -> None:
    suite = _suite([{"name": "stop_recall", "kind": "custom", "threshold": 0.0}])
    results: list[engine.Scored] = [
        (_case(i, {"label": "paid"}), TargetResult(output={"label": "paid"}), None)
        for i in range(12)
    ]
    report = engine.score(suite, results, mode="offline")
    assert not report.metrics["stop_recall"].passed
    assert not engine.gate(report, None).passed


def test_macro_f1_is_computed_over_the_split_and_counters_keep_their_totals() -> None:
    suite = _suite(
        [
            {
                "name": "reply_macro_f1",
                "kind": "custom",
                "params": {"corpus": "macro_f1"},
                "threshold": 0.7,
            },
            {"name": "pii_leaked", "kind": "custom", "direction": "lower", "threshold": 0},
        ]
    )
    pairs = [("stop", "stop"), ("stop", "other"), ("paid", "paid"), ("other", "other")]
    results: list[engine.Scored] = [
        (
            _case(i, {"label": gold}),
            TargetResult(output={"label": pred, "pii_leaks": ["x", "y"] if i == 0 else []}),
            None,
        )
        for i, (gold, pred) in enumerate(pairs)
    ]
    report = engine.score(suite, results, mode="offline")
    assert report.metrics["reply_macro_f1"].value == pytest.approx(7 / 9)
    assert report.metrics["pii_leaked"].value == pytest.approx(0.25)  # 1 of 4 cases leaked
    assert report.extensions is not None
    assert report.extensions["counts"] == {"pii_leaked": 2}
    check = engine.gate(report, None)
    assert [f.metric for f in check.failures] == ["pii_leaked"]


def test_an_errored_case_fails_the_gate() -> None:
    suite = _suite([{"name": "pii_leaked", "kind": "custom", "direction": "lower", "threshold": 1}])
    results: list[engine.Scored] = [
        (_case(i, {}), TargetResult(output={}), None) for i in range(11)
    ]
    results.append((_case(11, {}), None, "TimeoutError: "))
    report = engine.score(suite, results, mode="offline")
    check = engine.gate(report, None)
    assert not check.passed
    assert [(f.metric, f.reason) for f in check.failures] == [("cases", "error")]


def _replies(pairs: list[tuple[str, str]]) -> Any:
    suite = _suite(
        [
            {
                "name": "reply_macro_f1",
                "kind": "custom",
                "params": {"corpus": "macro_f1"},
                "threshold": 0.0,
            },
            {"name": "stop_recall", "kind": "custom", "threshold": 0.0},
        ]
    )
    results: list[engine.Scored] = [
        (_case(i, {"label": gold}), TargetResult(output={"label": pred}), None)
        for i, (gold, pred) in enumerate(pairs)
    ]
    return engine.score(suite, results, mode="offline")


GOOD = [("stop", "stop"), ("paid", "paid"), ("dispute", "dispute"), ("other", "other")] * 10


def test_a_small_drop_is_tolerated_and_a_point_macro_f1_regression_fails() -> None:
    base = _replies(GOOD)
    assert engine.gate(_replies(GOOD), base).passed
    # One dispute read as other out of 40 rows: correctness drops 2.5 pp, but the paired CI
    # still includes 0, and macro-F1 drops about 2.6 pp: the point check catches it.
    worse = list(GOOD)
    worse[2] = ("dispute", "other")
    check = engine.gate(_replies(worse), base)
    assert ("reply_macro_f1", "regression") in [(f.metric, f.reason) for f in check.failures]
    comparison = next(c for c in check.comparisons if c.metric == "reply_macro_f1")
    assert comparison.baseline == pytest.approx(base.metrics["reply_macro_f1"].value)


def test_a_gated_metric_missing_from_the_candidate_fails() -> None:
    base = _replies(GOOD)
    report = _replies(GOOD)
    metrics = {k: v for k, v in report.metrics.items() if k != "stop_recall"}
    report = report.model_copy(update={"metrics": metrics})
    check = engine.gate(report, base)
    assert ("stop_recall", "error") in [(f.metric, f.reason) for f in check.failures]


def test_a_drop_below_one_point_is_tolerated() -> None:
    many = GOOD * 5  # 200 rows
    base = _replies(many)
    # One dispute read as other in 200 rows: macro-F1 and correctness each drop about 0.5 pp.
    worse = list(many)
    worse[2] = ("dispute", "other")
    report = _replies(worse)
    drop = base.metrics["reply_macro_f1"].value - report.metrics["reply_macro_f1"].value
    assert 0 < drop * 100 < engine.MAX_REGRESSION_PP
    assert engine.gate(report, base).passed

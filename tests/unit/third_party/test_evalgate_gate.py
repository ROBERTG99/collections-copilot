"""evalgate fallback: thresholds, empty metrics, missing metrics and regression gating."""

from __future__ import annotations

from datetime import UTC, datetime

from evalgate.gate import check, paired_bootstrap
from evalgate.models import (
    CaseResult,
    DatasetRow,
    MetricResult,
    MetricSpec,
    Report,
    Suite,
    TargetResult,
)
from evalgate.runner import build_report


def _suite(metrics: list[MetricSpec]) -> Suite:
    return Suite.model_validate(
        {
            "schema": "evalgate.suite.v1",
            "name": "s",
            "dataset": "d.jsonl",
            "target": {"python": "m:f"},
            "metrics": [m.model_dump() for m in metrics],
        }
    )


def _report(values: list[float], name: str = "acc", threshold: float = 0.8) -> Report:
    mean = sum(values) / len(values)
    return Report(
        suite="s",
        run_id="r",
        created_at=datetime.now(UTC),
        n_cases=len(values),
        metrics={
            name: MetricResult(
                value=mean, threshold=threshold, direction="higher", passed=mean >= threshold
            )
        },
        cases=[
            CaseResult(id=str(i), split="test", scores={name: v}, passed=True)
            for i, v in enumerate(values)
        ],
    )


def test_threshold_failure() -> None:
    result = check(_report([1, 0, 0, 1]), "sha")
    assert not result.passed
    assert result.failures[0].reason == "threshold"


def test_metric_without_applicable_cases_never_passes() -> None:
    spec = MetricSpec(
        name="violations",
        kind="exact",
        direction="lower",
        threshold=0,
        aggregate="sum",
        params={"actual": "output.nope", "expected": "expected.nope", "skip_missing": True},
    )
    row = DatasetRow(id="1", split="test", input={}, expected={}, tags=[], meta={})
    report = build_report(_suite([spec]), [(row, TargetResult(output={}), None)])
    assert report.metrics["violations"].n == 0
    assert report.metrics["violations"].passed is False


def test_missing_gated_metric_fails() -> None:
    spec = MetricSpec(name="other", kind="exact", threshold=0.5)
    result = check(_report([1, 1]), "sha", suite=_suite([spec]))
    assert any(f.metric == "other" and f.reason == "error" for f in result.failures)


def test_regression_detected_and_small_drop_tolerated() -> None:
    base = _report([1.0] * 150)
    worse = _report([1.0] * 140 + [0.0] * 10)
    failed = check(worse, "a", base, "b", max_regression_pp=1.0)
    assert any(f.reason == "regression" for f in failed.failures)
    slight = _report([1.0] * 149 + [0.0])
    ok = check(slight, "a", base, "b", max_regression_pp=1.0)
    assert not any(f.reason == "regression" for f in ok.failures)


def test_bootstrap_is_deterministic() -> None:
    a = paired_bootstrap([1, 0, 1, 1], [1, 1, 0, 1], 500, 7)
    assert a == paired_bootstrap([1, 0, 1, 1], [1, 1, 0, 1], 500, 7)

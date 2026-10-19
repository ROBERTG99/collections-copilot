"""The eval engine: evalgate v0.1.0 plus the two things this repository needs on top of it.

evalgate loads the suites and datasets, runs the targets, scores every case, builds the
`evalgate.report.v1` documents and applies the gate (thresholds, budget, run health and the
paired-bootstrap regression check against a baseline). Its metrics are per-case scores in [0, 1]
averaged over every case, so two kinds of metric are finished here, in `finalize`:

- **Scoped metrics** score only the rows they apply to (STOP recall, write-back recall,
  citations, promise extraction, sends when allowed). The metric marks the other rows with
  `not_applicable()`; `finalize` leaves them out of the suite value, so a recall is not inflated
  by rows that have nothing to recall, and a metric with no applicable row fails its gate. The
  case scores keep those rows at the neutral 1.0 (evalgate's own `skip_missing` convention), so
  the regression check pairs every case and meets evalgate's minimum of 10 pairs.
- **Corpus metrics** (`params: {corpus: macro_f1}`) are computed over the whole split from the
  `gold` and `pred` details of each case. The per-case score kept in the report is the case's
  correctness, which is what the regression check pairs.

Counters (violations, write-backs, leaks) score 1 for a case with at least one event and keep the
raw count in `details.count`; `finalize` sums them into `extensions.counts`. A gate of
`≤ 0` on the mean is the same gate as a sum of 0.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evalgate.contracts import (
    Case,
    CheckFailure,
    CheckResult,
    MetricScore,
    MetricSummary,
    Report,
    Suite,
    TargetResult,
)
from evalgate.ids import uuid7
from evalgate.metrics.base import worst_score
from evalgate.metrics.registry import build_metric
from evalgate.report.build import CaseOutcome, build_report
from evalgate.runner.engine import RunOptions, run_suite_async
from evalgate.stats.bootstrap import mean_ci
from evalgate.stats.gate import check
from evalgate.suite.loader import LoadedSuite, load_suite

from collections_copilot.evals import metrics as _metrics  # registers the metrics on import
from collections_copilot.evals.metrics import NOT_APPLICABLE, macro_f1

SEED = 1337
RESAMPLES = 10_000
CASE_TIMEOUT_S = 30.0
MAX_REGRESSION_PP = 1.0

Scored = tuple[Case, TargetResult | None, str | None]

__all__ = ["LoadedSuite", "finalize", "gate", "load_suite", "run", "score"]


def _corpus_value(kind: str, scores: list[MetricScore | None]) -> float:
    if kind != "macro_f1":
        raise ValueError(f"unknown corpus metric {kind!r}")
    gold = [s.details.get("gold") if s else None for s in scores]
    pred = [s.details.get("pred") if s else None for s in scores]
    return macro_f1(gold, pred, _metrics.REPLY_LABELS)


def _passes(value: float, threshold: float | None, direction: str) -> bool:
    if threshold is None:
        return True
    return value >= threshold if direction == "higher" else value <= threshold


def finalize(suite: Suite, outcomes: list[CaseOutcome], report: Report) -> Report:
    """Recompute scoped and corpus metrics and add the counters (see the module docstring)."""
    ordered = sorted(outcomes, key=lambda o: o.case_id)
    cases = {c.id: c.model_copy(deep=True) for c in report.cases}
    metrics = dict(report.metrics)
    applicable: dict[str, int] = {}
    counts: dict[str, int] = {}
    for spec in suite.metrics:
        found = [next((s for s in o.scores if s.name == spec.name), None) for o in ordered]
        kept: list[MetricScore | None] = []
        for score in found:
            if score is None or not score.details.get(NOT_APPLICABLE):
                kept.append(score)
        applicable[spec.name] = len(kept)
        if any(s is not None and "count" in s.details for s in found):
            counts[spec.name] = sum(int(s.details.get("count", 0)) for s in found if s is not None)
        corpus = spec.params.get("corpus")
        if not kept:  # no evidence: the worst value, and the gate fails
            value, low, high, passed = worst_score(spec.direction), None, None, False
        elif corpus:
            value, low, high = _corpus_value(str(corpus), kept), None, None
            passed = _passes(value, spec.threshold, spec.direction)
        else:
            values = [s.value if s is not None else worst_score(spec.direction) for s in kept]
            ci = mean_ci(values, seed=SEED, resamples=RESAMPLES)
            value, low, high = ci.estimate, ci.low, ci.high
            passed = _passes(value, spec.threshold, spec.direction)
        metrics[spec.name] = MetricSummary(
            value=round(value, 10),
            threshold=spec.threshold,
            direction=spec.direction,
            passed=passed,
            ci_low=None if low is None else round(low, 10),
            ci_high=None if high is None else round(high, 10),
        )
    extensions = {**(report.extensions or {}), "applicable": applicable, "counts": counts}
    return report.model_copy(
        update={
            "metrics": metrics,
            "cases": [cases[c.id] for c in report.cases],
            "extensions": extensions,
        }
    )


async def run(path: str | Path, split: str) -> tuple[LoadedSuite, Report]:
    """Run one suite on one split with evalgate's runner (no run store, no response cache)."""
    loaded = load_suite(path, split)
    options = RunOptions(
        split=split,
        cache_mode="off",
        use_store=False,
        seed=SEED,
        resamples=RESAMPLES,
        case_timeout_s=CASE_TIMEOUT_S,
        retries=0,  # offline targets are deterministic; a retry would only hide a flaky case
    )
    result = await run_suite_async(loaded, options)
    return loaded, finalize(loaded.suite, result.outcomes, result.report)


def score(suite: Suite, results: Sequence[Scored], *, mode: str) -> Report:
    """Score results produced outside evalgate's runner (the budgeted live loop)."""
    metrics = [build_metric(spec) for spec in suite.metrics]
    gated = {m.name for m in suite.metrics if m.gate}
    direction = {m.name: m.direction for m in suite.metrics}
    errors = dict.fromkeys(direction, 0)
    outcomes: list[CaseOutcome] = []
    for case, result, error in results:
        scores: list[MetricScore] = []
        for metric in metrics:
            worst = worst_score(direction[metric.name])
            if error is not None or result is None:
                scores.append(MetricScore(name=metric.name, value=worst, passed=False))
                continue
            try:
                scores.append(metric.score(case, result))
            except Exception as exc:  # a broken metric fails closed, as in evalgate's runner
                errors[metric.name] += 1
                scores.append(
                    MetricScore(
                        name=metric.name,
                        value=worst,
                        passed=False,
                        details={
                            "error": f"{type(exc).__name__}: {exc}"[:300],
                            "metric_error": True,
                        },
                    )
                )
        outcomes.append(
            CaseOutcome(
                case_id=case.id,
                split=case.split,
                scores=scores,
                passed=error is None and all(s.passed for s in scores if s.name in gated),
                cost_usd=result.cost_usd if result else 0.0,
                latency_ms=result.latency_ms if result else 0.0,
                error=error,
                tags=case.tags,
            )
        )
    n = len(outcomes)
    error_rate = sum(1 for o in outcomes if o.error) / n if n else 0.0
    extensions: dict[str, Any] = {
        "run_health": "degraded" if error_rate > 0.5 else "ok",
        "error_rate": round(error_rate, 6),
        "gates": {m.name: m.gate for m in suite.metrics},
        "metric_errors": {k: v for k, v in errors.items() if v},
        "budget": suite.budget.model_dump() if suite.budget else None,
        "seed": SEED,
        "resamples": RESAMPLES,
    }
    report = build_report(
        suite,
        outcomes,
        run_id=uuid7(),
        mode=mode,
        models=[],
        prompt_hashes={},
        seed=SEED,
        resamples=RESAMPLES,
        extensions=extensions,
    )
    return finalize(suite, outcomes, report)


def digest(report: Report) -> str:
    return hashlib.sha256(report.model_dump_json(by_alias=True).encode()).hexdigest()


def gate(report: Report, baseline: Report | None, suite: Suite | None = None) -> CheckResult:
    """evalgate's gate, plus: every errored case fails the run (not only an error rate > 50%),
    and so does a gated metric with no applicable row.

    With `suite`, the suite's budget is the one applied (the live gate passes a suite without
    the offline latency budget).
    """
    if suite is not None:
        budget = suite.budget.model_dump() if suite.budget else None
        report = report.model_copy(
            update={"extensions": {**(report.extensions or {}), "budget": budget}}
        )
    result = check(
        report,
        report_sha256=digest(report),
        baseline=baseline,
        baseline_sha256=digest(baseline) if baseline else None,
        max_regression_pp=MAX_REGRESSION_PP,
        seed=SEED,
        resamples=RESAMPLES,
    )
    failures = list(result.failures)
    ext = report.extensions or {}
    gates = ext.get("gates") or {}
    applicable = ext.get("applicable") or {}
    for name in report.metrics:  # a gated metric with no applicable row has no evidence
        if gates.get(name, True) and applicable.get(name, 1) == 0:
            failures.append(CheckFailure(metric=name, reason="error", value=0.0))
    errored = sum(1 for c in report.cases if c.error)
    if errored:
        failures.append(CheckFailure(metric="cases", reason="error", value=errored))
    if len(failures) != len(result.failures):
        result = result.model_copy(update={"passed": False, "failures": failures})
    return result


def main(argv: list[str] | None = None) -> int:
    """`python -m collections_copilot.evals.engine --suite replies --split dev`: one suite, one
    split, the suite numbers printed (the tuning loop; `make eval` is the gate)."""
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description=(main.__doc__ or "").splitlines()[0])
    parser.add_argument("--suite", required=True, choices=["nba", "replies", "compliance"])
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    args = parser.parse_args(argv)
    _, report = asyncio.run(run(Path("evals/suites") / f"{args.suite}.yaml", args.split))
    for name, summary in report.metrics.items():
        print(f"{name}: {summary.value:.4f} (threshold {summary.threshold}, {summary.direction})")
    failed = [c.id for c in report.cases if not c.passed]
    print(f"{len(failed)} of {report.n_cases} {args.split} cases failed: {', '.join(failed[:20])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

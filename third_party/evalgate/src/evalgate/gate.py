"""Gate: thresholds, budgets, case errors and paired-bootstrap regression against a baseline."""

from __future__ import annotations

import hashlib
import os
import random
from pathlib import Path

from evalgate.models import CheckResult, Comparison, Failure, Report, Suite


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def paired_bootstrap(
    base: list[float], cand: list[float], resamples: int, seed: int
) -> tuple[float, float]:
    """95% CI of mean(cand - base) in the metric's units."""
    n = len(base)
    if n == 0:
        return 0.0, 0.0
    diffs = [c - b for b, c in zip(base, cand, strict=True)]
    rng = random.Random(seed)  # noqa: S311 - statistics, not security
    means = []
    for _ in range(resamples):
        total = 0.0
        for _ in range(n):
            total += diffs[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    return means[int(0.025 * (resamples - 1))], means[int(0.975 * (resamples - 1))]


def check(
    report: Report,
    report_sha256: str,
    baseline: Report | None = None,
    baseline_sha256: str | None = None,
    max_regression_pp: float = 1.0,
    suite: Suite | None = None,
    resamples: int = 2000,
) -> CheckResult:
    seed = int(os.environ.get("EVALGATE_BOOTSTRAP_SEED", "1337"))
    failures: list[Failure] = []
    comparisons: list[Comparison] = []
    for name, mr in report.metrics.items():
        if mr.gate and not mr.passed:
            failures.append(
                Failure(metric=name, reason="threshold", value=mr.value, threshold=mr.threshold)
            )
    if suite is not None:
        for spec in suite.metrics:
            if spec.gate and spec.name not in report.metrics:
                failures.append(Failure(metric=spec.name, reason="error", value=None))
    errored = [c.id for c in report.cases if c.error]
    if errored:
        failures.append(Failure(metric="cases", reason="error", value=float(len(errored))))
    if suite and suite.budget:
        b = suite.budget
        if b.max_cost_usd is not None and report.cost_usd_total > b.max_cost_usd:
            failures.append(
                Failure(
                    metric="cost_usd_total",
                    reason="budget_cost",
                    value=report.cost_usd_total,
                    threshold=b.max_cost_usd,
                )
            )
        if b.max_p95_ms is not None and report.latency_p95_ms > b.max_p95_ms:
            failures.append(
                Failure(
                    metric="latency_p95_ms",
                    reason="budget_p95",
                    value=report.latency_p95_ms,
                    threshold=b.max_p95_ms,
                )
            )
    if baseline is not None:
        base_cases = {c.id: c for c in baseline.cases}
        for name, bm in baseline.metrics.items():
            if bm.gate and name not in report.metrics:
                failures.append(Failure(metric=name, reason="error", value=None))
        for name, mr in report.metrics.items():
            if name not in baseline.metrics:
                continue
            pairs = [
                (base_cases[c.id].scores[name], c.scores[name])
                for c in report.cases
                if c.id in base_cases and name in c.scores and name in base_cases[c.id].scores
            ]
            sign = 1.0 if mr.direction == "higher" else -1.0
            delta = (mr.value - baseline.metrics[name].value) * 100
            lo = hi = delta
            if pairs:
                lo_u, hi_u = paired_bootstrap(
                    [p[0] for p in pairs], [p[1] for p in pairs], resamples, seed
                )
                lo, hi = lo_u * 100, hi_u * 100
            comparisons.append(
                Comparison(
                    metric=name,
                    baseline=baseline.metrics[name].value,
                    candidate=mr.value,
                    delta_pp=round(delta, 4),
                    ci_low_pp=round(lo, 4),
                    ci_high_pp=round(hi, 4),
                    n_paired=len(pairs),
                )
            )
            worse = sign * delta < -max_regression_pp
            ci_excludes_zero = (sign * hi < 0) if sign > 0 else (sign * lo < 0)
            if mr.gate and worse and (ci_excludes_zero or not pairs):
                failures.append(
                    Failure(
                        metric=name,
                        reason="regression",
                        value=mr.value,
                        delta_pp=round(delta, 4),
                        ci_low_pp=round(lo, 4),
                        ci_high_pp=round(hi, 4),
                    )
                )
    return CheckResult(
        report_sha256=report_sha256,
        baseline_sha256=baseline_sha256,
        max_regression_pp=max_regression_pp,
        seed=seed,
        resamples=resamples,
        passed=not failures,
        failures=failures,
        comparisons=comparisons,
    )

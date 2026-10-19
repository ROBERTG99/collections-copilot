"""`make eval-live`: the suites with real models, within LIVE_EVAL_BUDGET_USD (BUILD.md §11).

Without `ANTHROPIC_API_KEY` nothing runs and the exit code is 0. With a key it prints the
estimated cost of each suite, then runs the test splits one case at a time through the Claude
API and stops before any case that would cross the budget. Results go to `evals/reports/live.json`
(committed) and the live section of `evals/REPORT.md`. `--record-fixtures` (`make
record-fixtures`) also saves every response as a cassette that offline runs replay.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evalgate.calibrate import calibrate
from evalgate.loader import load_dataset, load_labels, load_rubric, load_suite
from evalgate.models import DatasetRow, Report, Suite, TargetResult
from evalgate.runner import build_report, langgraph_target, python_target

from collections_copilot.agent.prompts import prompt_names
from collections_copilot.config import Settings, get_settings
from collections_copilot.evals import judge
from collections_copilot.evals import report as offline
from collections_copilot.llm.gateway import routes
from collections_copilot.llm.pricing import cost_usd, estimate_usd
from collections_copilot.llm.providers import CASSETTES, wrapping_providers
from collections_copilot.llm.providers.base import Provider, ProviderRequest, ProviderResponse
from collections_copilot.llm.providers.fake import FakeLLM

Target = Callable[[dict[str, Any]], Awaitable[TargetResult]]

LIVE_SUMMARY = offline.REPORTS_DIR / "live.json"
SUMMARY_SCHEMA = "collections-copilot.live-evals.v1"
DEFAULT_BUDGET_USD = 2.0
COST_PER_ACCOUNT_MAX_USD = 0.02  # §11, live only: the NBA suite's cost per case
LIVE_TIMEOUT_S = 120.0  # real models with adaptive thinking; the suites' 30 s is for offline
# Cheapest first, so a small budget still measures something.
ORDER = ("replies", "tone", "compliance", "nba")
CALLS_PER_CASE = {
    "replies": ("classify_reply",),
    "tone": ("judge_tone",),
    "compliance": ("prioritize_rationale", "next_best_action", "draft_message"),
    "nba": ("prioritize_rationale", "next_best_action", "draft_message"),
}
# A conservative guess per call (adaptive thinking included); the real cost replaces it after
# the first case.
TOKENS_PER_CALL = (3_000, 1_500)


class BudgetExhaustedError(RuntimeError):
    """A provider call could cost more than what is left of LIVE_EVAL_BUDGET_USD."""


@dataclass
class BudgetGuard:
    limit_usd: float
    spent_usd: float = 0.0

    def can_afford(self, cost: float) -> bool:
        return self.spent_usd + cost <= self.limit_usd + 1e-12

    def charge(self, cost: float) -> None:
        self.spent_usd += cost


@dataclass
class Meter:
    """Cost of every provider response, in USD. With a guard, every call is checked first."""

    cost_usd: float = 0.0
    calls: int = 0
    guard: BudgetGuard | None = None

    def add(self, cost: float) -> None:
        self.cost_usd += cost
        self.calls += 1
        if self.guard is not None:
            self.guard.charge(cost)


def worst_case_usd(request: ProviderRequest) -> float:
    """Upper bound for one call: the prompt at 3 characters per token, plus max_tokens out."""
    tokens_in = (len(request.system) + len(request.user)) // 3 + 1
    return estimate_usd(request.model, tokens_in, request.max_tokens)


class _Metered:
    def __init__(self, inner: Provider, meter: Meter, record_dir: Path | None) -> None:
        self.inner = inner
        self.name = inner.name
        self.meter = meter
        self.record_dir = record_dir

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        worst = worst_case_usd(request)
        guard = self.meter.guard
        if guard is not None and not guard.can_afford(worst):
            raise BudgetExhaustedError(f"a {request.route} call may cost up to USD {worst:.4f}")
        try:
            response = await self.inner.complete(request)
        except asyncio.CancelledError:
            self.meter.add(worst)  # cancelled mid-flight (timeout) but possibly billed
            raise
        self.meter.add(cost_usd(response.model or request.model, response.usage))
        if self.record_dir is not None:
            path = self.record_dir / request.route / f"{FakeLLM.request_key(request)}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "text": response.text,
                        "stop_reason": response.stop_reason,
                        "model": response.model,
                        "usage": response.usage.__dict__,
                        "refusal_category": response.refusal_category,
                    },
                    ensure_ascii=False,
                    indent=1,
                )
            )
        return response


def wrap_provider(inner: Provider, *, meter: Meter, record_dir: Path | None = None) -> Provider:
    return _Metered(inner, meter, record_dir)


def _exhausted(exc: BaseException) -> bool:
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, BudgetExhaustedError):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


@dataclass
class LiveRun:
    report: Report
    stopped_at_budget: bool
    extra: dict[str, Any] = field(default_factory=dict)


async def run_budgeted(
    suite: Suite,
    rows: list[DatasetRow],
    target: Target,
    guard: BudgetGuard,
    per_case_estimate_usd: float,
    meter: Meter | None = None,
    timeout_s: float | None = None,
) -> LiveRun:
    """Run cases one by one and stop at the budget.

    A case starts only if the expected cost of a case fits what is left. With a metered
    provider (`meter.guard is guard`) every model call is also checked against its worst case
    before it is sent, so the budget holds even when one case costs far more than expected; the
    case that hit the limit is dropped, not scored.
    """
    results: list[tuple[DatasetRow, TargetResult, str | None]] = []
    metered = meter is not None and meter.guard is guard
    spent = 0.0
    stopped = False
    for row in rows:
        expected = max(per_case_estimate_usd, spent / len(results) if results else 0.0)
        if not guard.can_afford(expected):
            stopped = True
            break
        before = meter.cost_usd if meter else 0.0
        started = time.perf_counter()
        try:
            res = await asyncio.wait_for(target(row.input), timeout_s or suite.timeout_s)
            err = None
        except Exception as exc:  # a failing case never aborts the run
            if _exhausted(exc):
                stopped = True
                break
            res, err = TargetResult(output={}), f"{type(exc).__name__}: {exc}"
        res.latency_ms = res.latency_ms or (time.perf_counter() - started) * 1000
        if meter is not None:  # graph targets do not report cost themselves
            res.cost_usd = max(res.cost_usd, meter.cost_usd - before)
        if not metered:
            guard.charge(res.cost_usd)
        spent += res.cost_usd
        results.append((row, res, err))
    report = build_report(suite, results, mode="live")
    report.extensions["stopped_at_budget"] = stopped
    return LiveRun(report, stopped)


def live_failures(name: str, report: Report, suite: Suite, stopped: bool) -> list[str]:
    """Live gate: the suite's thresholds without its offline latency budget (§11: "graph p95
    offline ≤ 300 ms"), plus NBA cost per account ≤ USD 0.02. A stopped suite is not gated."""
    if stopped:
        return []
    failures = offline.gate(report, None, suite.model_copy(update={"budget": None})).failures
    reasons = [f"{f.metric}: {f.reason}" for f in failures]
    if name == "nba" and report.cost_usd_per_case > COST_PER_ACCOUNT_MAX_USD:
        reasons.append("cost_usd_per_case: budget_cost")
    return reasons


def per_case_estimate(name: str, settings: Settings) -> float:
    table = routes(settings)
    tokens_in, tokens_out = TOKENS_PER_CALL
    return sum(
        estimate_usd(table[route].model, tokens_in, tokens_out) for route in CALLS_PER_CASE[name]
    )


def _rows(name: str, split: str) -> int:
    if name == "tone":
        return len(load_labels(judge.LABELS, split))
    return len(load_dataset(load_suite(offline.SUITES_DIR / f"{name}.yaml").dataset, split))


def _target(suite: Suite) -> Target:
    if suite.target.python:
        return python_target(suite.target.python)
    return langgraph_target(suite.target.langgraph or "")


async def _tone(guard: BudgetGuard, meter: Meter, split: str, estimate: float) -> dict[str, Any]:
    rubric = load_rubric(judge.RUBRIC)
    labels = load_labels(judge.LABELS, split)
    gw = judge.gateway()
    predictions: dict[str, int] = {}
    stopped = False
    start = meter.cost_usd  # this suite's own spend, not earlier suites'
    for row in labels:
        done = len(predictions)
        if not guard.can_afford(max(estimate, (meter.cost_usd - start) / done if done else 0.0)):
            stopped = True
            break
        try:
            predictions[row.id] = await judge.judge_tone(row, gw)
        except Exception as exc:
            if not _exhausted(exc):
                raise
            stopped = True
            break
    judged = [row for row in labels if row.id in predictions]
    result = calibrate(rubric, judged, lambda row: predictions[row.id])
    return {
        "n_cases": result.n,
        "agreement": result.agreement,
        "kappa": result.kappa,
        "passed": result.passed and not stopped,
        "stopped_at_budget": stopped,
    }


async def run_live(
    names: list[str], split: str, guard: BudgetGuard, *, record: bool
) -> dict[str, Any]:
    settings = get_settings()
    meter = Meter(guard=guard)  # every model call is checked against the budget first
    summary: dict[str, Any] = {
        "schema": SUMMARY_SCHEMA,
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_sha": offline.git_sha(),
        "prompt_hashes": offline.prompt_hashes(sorted(prompt_names())),
        "provider": settings.llm_provider,
        "models": offline.models_for(settings),
        "split": split,
        "budget_usd": guard.limit_usd,
        "suites": {},
    }
    record_dir = CASSETTES if record else None
    with wrapping_providers(lambda p: wrap_provider(p, meter=meter, record_dir=record_dir)):
        for name in names:
            estimate = per_case_estimate(name, settings)
            if name == "tone":
                summary["suites"]["tone"] = await _tone(guard, meter, split, estimate)
                continue
            suite = load_suite(offline.SUITES_DIR / f"{name}.yaml")
            rows = load_dataset(suite.dataset, split)
            run = await run_budgeted(
                suite, rows, _target(suite), guard, estimate, meter, timeout_s=LIVE_TIMEOUT_S
            )
            rep = run.report
            rep.models = offline.models_for(settings)
            rep.prompt_hashes = offline.prompt_hashes(suite.prompts)
            reasons = live_failures(name, rep, suite, run.stopped_at_budget)
            summary["suites"][name] = {
                "n_cases": rep.n_cases,
                "of": len(rows),
                "stopped_at_budget": run.stopped_at_budget,
                "metrics": {k: v.value for k, v in rep.metrics.items()},
                "cost_usd_total": rep.cost_usd_total,
                "cost_usd_per_case": rep.cost_usd_per_case,
                "latency_p95_ms": rep.latency_p95_ms,
                "passed": not run.stopped_at_budget and not reasons,
                "failures": reasons,
                # Reported for partial runs too; gated only when the suite finished.
                "cost_gate_usd_per_case": COST_PER_ACCOUNT_MAX_USD if name == "nba" else None,
            }
    summary["spent_usd"] = round(guard.spent_usd, 6)
    return summary


def render_summary(summary: dict[str, Any]) -> str:
    """The live section of evals/REPORT.md."""
    lines = [
        f"Mode **live**: run {summary['date']} with {summary['provider']} "
        f"({', '.join(summary['models'])}) on the `{summary['split']}` split; spent USD "
        f"{summary['spent_usd']:.4f} of a USD {summary['budget_usd']:.2f} budget.",
        "",
        "| Suite | Cases | Result | Key numbers | Cost USD per case |",
        "|---|---|---|---|---|",
    ]
    for name, s in summary["suites"].items():
        if name == "tone":
            numbers = f"agreement {s['agreement']:.4f}, kappa {s['kappa']:.4f} (gate ≥ 0.8)"
            cost = ""
            cases = str(s["n_cases"])
        else:
            numbers = ", ".join(f"{k} {v:.4f}" for k, v in s["metrics"].items())
            cost = f"{s['cost_usd_per_case']:.4f}"
            cases = f"{s['n_cases']} of {s['of']}"
        result = (
            "stopped at budget (not gated)"
            if s["stopped_at_budget"]
            else ("PASS" if s["passed"] else "FAIL: " + "; ".join(s.get("failures", [])))
        )
        lines.append(f"| `{name}` | {cases} | {result} | {numbers} | {cost} |")
    return "\n".join(lines)


def load_summary(path: Path = LIVE_SUMMARY) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--suite", action="append", choices=ORDER, dest="suites")
    parser.add_argument("--split", choices=["dev", "test"], default="test")
    parser.add_argument("--record-fixtures", action="store_true")
    args = parser.parse_args(argv)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "Live evals were not run: ANTHROPIC_API_KEY is not set. "
            "`make eval` gives the offline numbers."
        )
        return 0
    budget = float(os.environ.get("LIVE_EVAL_BUDGET_USD") or DEFAULT_BUDGET_USD)
    settings = Settings(llm_provider="anthropic")
    names = [n for n in ORDER if n in (args.suites or ORDER)]
    plan = [(n, _rows(n, args.split), per_case_estimate(n, settings)) for n in names]
    total = sum(rows * each for _, rows, each in plan)
    print(f"Estimated cost (USD, {args.split} split, budget {budget:.2f}):")
    for name, rows, each in plan:
        print(f"  {name:<11} {rows:>4} cases x {each:.4f} = {rows * each:.4f}")
    print(f"  total {total:.4f}")
    first = plan[0][2] if plan else 0.0
    if first > budget:
        print(
            f"The first case alone ({first:.4f}) exceeds LIVE_EVAL_BUDGET_USD ({budget:.2f}); "
            "nothing was run."
        )
        return 0
    if total > budget:
        print("The full run exceeds LIVE_EVAL_BUDGET_USD; it stops before the case that would.")
    os.environ["CC_LLM_PROVIDER"] = "anthropic"  # the targets build their own provider
    get_settings.cache_clear()
    summary = asyncio.run(
        run_live(names, args.split, BudgetGuard(budget), record=args.record_fixtures)
    )
    LIVE_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    LIVE_SUMMARY.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    print(render_summary(summary))
    print(f"wrote {LIVE_SUMMARY}; run `make eval` to refresh evals/REPORT.md")
    completed = [s for s in summary["suites"].values() if not s["stopped_at_budget"]]
    return 0 if all(s["passed"] for s in completed) else 1

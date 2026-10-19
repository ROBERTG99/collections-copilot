"""`make eval`: run every suite, gate it and generate `evals/REPORT.md` (BUILD.md §11).

The test split is gated by evalgate v0.1.0 (thresholds, the suite budget, run health and a
regression of more than 1 percentage point against `evals/reports/baseline.json`) plus "no
errored case" (`collections_copilot.evals.engine.gate`); the dev split, the only one used for
tuning, is reported for information. Every number carries its mode: offline numbers come
from the deterministic code plus FakeLLM, live numbers only from `make eval-live`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from evalgate.contracts import CheckResult, Report, Suite
from evalgate.report.markdown import MARKER, render_markdown

from collections_copilot.agent.prompts import load_prompt, prompt_names
from collections_copilot.config import Settings, get_settings
from collections_copilot.evals import engine
from collections_copilot.evals.judge import CalibrationResult
from collections_copilot.llm.providers import wrapping_providers
from collections_copilot.llm.providers.base import Provider, ProviderRequest, ProviderResponse

SUITES = ("nba", "replies", "compliance")
GATED_SPLIT = "test"
MAX_REGRESSION_PP = engine.MAX_REGRESSION_PP
SUITES_DIR = Path("evals/suites")
REPORTS_DIR = Path("evals/reports")
BASELINE = REPORTS_DIR / "baseline.json"
REPORT_MD = Path("evals/REPORT.md")
BASELINE_SCHEMA = "collections-copilot.eval-baselines.v1"
MANIFEST = Path("evals/datasets/MANIFEST.json")
# The prompts behind each suite's numbers (their hashes go into the report).
SUITE_PROMPTS = {
    "nba": ["next_best_action", "draft_message", "prioritize_rationale"],
    "replies": ["classify_reply"],
    "compliance": ["next_best_action", "draft_message", "prioritize_rationale"],
}


@dataclass
class ReplayCounter:
    """Counts model calls and how many FakeLLM answered from a recorded live cassette."""

    calls: int = 0
    replayed: Counter[str] = field(default_factory=Counter)

    def wrap(self, inner: Provider) -> Provider:
        return _Counting(inner, self)


class _Counting:
    def __init__(self, inner: Provider, counter: ReplayCounter) -> None:
        self.inner = inner
        self.name = inner.name
        self.counter = counter

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        response = await self.inner.complete(request)
        self.counter.calls += 1
        if response.model and response.model != "fake":
            self.counter.replayed[response.model] += 1
        return response


def label_models(report: Report, counter: ReplayCounter) -> None:
    """Offline numbers name every model behind them: `fake`, plus any replayed cassette's."""
    report.models = sorted({"fake", *counter.replayed})
    report.extensions = {
        **(report.extensions or {}),
        "model_calls": counter.calls,
        "replayed_calls": sum(counter.replayed.values()),
    }


def git_sha() -> str | None:
    """HEAD, with `-dirty` when the working tree has uncommitted changes."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - git from PATH, fixed arguments
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return f"{sha}-dirty" if dirty else sha


@dataclass
class SuiteRun:
    name: str
    report: Report
    check: CheckResult
    dev: Report | None = None
    baseline: Report | None = None


def models_for(settings: Settings) -> list[str]:
    """The models behind the numbers: offline runs never claim a real model."""
    if settings.llm_provider == "fake":
        return ["fake"]
    if settings.llm_provider == "bedrock":
        return sorted({settings.bedrock_model_fast, settings.bedrock_model_reasoning})
    return sorted({settings.model_fast, settings.model_reasoning})


def prompt_hashes(names: list[str]) -> dict[str, str]:
    return {name: load_prompt(name).sha256 for name in names}


def load_baselines(path: Path = BASELINE) -> dict[str, Report]:
    """One baseline report per suite, in a single committed file."""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {name: Report.model_validate(rep) for name, rep in raw["suites"].items()}


def write_baselines(path: Path, reports: dict[str, Report]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": BASELINE_SCHEMA,
        "suites": {
            name: json.loads(rep.model_dump_json(by_alias=True))
            for name, rep in sorted(reports.items())
        },
    }
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")


def _save(path: Path, report: Report) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(by_alias=True, indent=1))


def gate(report: Report, baseline: Report | None, suite: Suite | None) -> CheckResult:
    return engine.gate(report, baseline, suite)


async def run_one(name: str, split: str, mode: str, settings: Settings) -> Report:
    counter = ReplayCounter()
    with wrapping_providers(counter.wrap):
        _, report = await engine.run(SUITES_DIR / f"{name}.yaml", split)
    report.mode = "live" if mode == "live" else "offline"
    report.git_sha = git_sha()
    report.prompt_hashes = prompt_hashes(SUITE_PROMPTS[name])
    report.extensions = {
        **(report.extensions or {}),
        "split": split,
        "provider": settings.llm_provider,
    }
    if settings.llm_provider == "fake":
        label_models(report, counter)
    else:
        report.models = models_for(settings)
    return report


def _demote(markdown: str) -> list[str]:
    """Nest evalgate's headings one level under this report's own (and drop its PR marker)."""
    return [
        f"#{line}" if line.startswith("#") else line
        for line in markdown.splitlines()
        if line != MARKER
    ]


def _dev_rows(runs: list[SuiteRun]) -> list[str]:
    rows = [
        "| Suite | Metric | Value | Threshold | Passed |",
        "|---|---|---|---|---|",
    ]
    for run in runs:
        if run.dev is None:
            continue
        for metric, mr in run.dev.metrics.items():
            threshold = "" if mr.threshold is None else f"{mr.threshold:g}"
            rows.append(
                f"| `{run.name}` | `{metric}` | {mr.value:.4f} | {threshold} | "
                f"{'yes' if mr.passed else 'NO'} |"
            )
        rows.append(
            f"| `{run.name}` | latency p95 ms | {run.dev.latency_p95_ms:.1f} | | |",
        )
    return rows


def tone_section(result: CalibrationResult, *, mode: str) -> str:
    """Judge calibration on tone_calibration.v1 (the gate is live only)."""
    return "\n".join(
        [
            f"## Tone judge calibration ({GATED_SPLIT} split, mode {mode}, informational)",
            "",
            f"`evals/rubrics/tone.yaml` against labels by construction: {result.n} drafts, "
            f"agreement {result.agreement:.4f}, Cohen's kappa {result.kappa:.4f}. The ≥ 0.8 "
            "agreement gate applies to the live judge only: offline, the judge is FakeLLM's "
            "keyword stub, which only recognises explicit threats.",
        ]
    )


def _short(sha: str | None) -> str:
    if not sha:
        return "unknown"
    return sha[:12] + ("-dirty" if sha.endswith("-dirty") else "")


def _provenance(baseline: Report | None) -> list[str]:
    lines = []
    if baseline is not None:
        lines.append(
            f"- Baseline: `{BASELINE}` (run `{baseline.run_id}`, commit "
            f"`{_short(baseline.git_sha)}`, {baseline.created_at})."
        )
    if MANIFEST.exists():
        datasets = json.loads(MANIFEST.read_text()).get("datasets", {})
        digests = ", ".join(
            f"`{name}` {str(meta.get('sha256', ''))[:12]}"
            for name, meta in sorted(datasets.items())
        )
        if digests:
            lines.append(f"- Dataset digests (`{MANIFEST}`): {digests}.")
    return lines


def _mode_line(runs: list[SuiteRun]) -> str:
    calls = sum(int((r.report.extensions or {}).get("model_calls", 0)) for r in runs)
    replayed = sum(int((r.report.extensions or {}).get("replayed_calls", 0)) for r in runs)
    models = sorted({m for r in runs for m in r.report.models}) or ["none"]
    mode = runs[0].report.mode if runs else "offline"
    line = f"- Every number below is mode **{mode}** (models: {', '.join(models)})"
    if replayed:
        line += (
            f"; FakeLLM replayed recorded live responses for {replayed} of {calls} model calls "
            "(`tests/fixtures/llm/cassettes/`), the rest are its keyword rules"
        )
    return line + ", unless a section says live."


def render(
    runs: list[SuiteRun],
    *,
    live_section: str | None,
    extra_sections: list[str] | None = None,
    baseline: Report | None = None,
) -> str:
    first = runs[0].report if runs else None
    mode = first.mode if first else "offline"
    lines = [
        "# Evaluation report",
        "",
        "Generated by `scripts/eval_report.py` (`make eval`); do not edit by hand.",
        "",
        f"- Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} at commit "
        f"`{_short(first.git_sha if first else None)}`.",
        _mode_line(runs),
        "- Engine: evalgate v0.1.0 (`collections_copilot.evals.engine` adds the scoped and "
        "corpus metrics and the counters).",
        f"- Gated split: `{GATED_SPLIT}`. Gate: each suite's thresholds and budget, no errored "
        f"case, and no regression above {MAX_REGRESSION_PP:g} percentage point against "
        f"`{BASELINE}` (paired bootstrap, 95% CI). Scoped metrics (recalls, citations) count "
        "only the rows they apply to; macro-F1 is computed over the split, and its regression is "
        "checked as a point value and on the per-case label correctness; counters show the share "
        "of cases with at least one event (the raw totals are under each suite).",
        "- Datasets: `evals/datasets/*.v1.jsonl`, labels by construction (seed 4242); the dev "
        "split is the only one used for tuning.",
        *_provenance(baseline),
        "",
        "| Suite | Split | Cases | Gate |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| `{r.name}` | {GATED_SPLIT} | {r.report.n_cases} | "
        f"{'PASS' if r.check.passed else 'FAIL'} |"
        for r in runs
    ]
    lines += ["", f"## Gated results ({GATED_SPLIT} split, mode {mode})", ""]
    for r in runs:
        lines += _demote(render_markdown(r.report, r.check, r.baseline))
        lines.append("")
        if r.report.prompt_hashes:
            hashes = ", ".join(f"`{k}` {v[:12]}" for k, v in sorted(r.report.prompt_hashes.items()))
            lines += [f"Prompt hashes: {hashes}.", ""]
        counts = (r.report.extensions or {}).get("counts") or {}
        if counts:
            totals = ", ".join(f"`{k}` {v}" for k, v in sorted(counts.items()))
            lines += [f"Raw totals ({GATED_SPLIT} split, mode {mode}): {totals}.", ""]
    if lines[-1] != "":
        lines.append("")
    if any(r.dev for r in runs):
        lines += [
            f"## Dev split (informational, the tuning split, mode {mode})",
            "",
            *_dev_rows(runs),
            "",
        ]
    for section in extra_sections or []:
        lines += [section.rstrip(), ""]
    lines += ["## Live evals", ""]
    lines += [
        live_section.rstrip()
        if live_section
        else "No live run is recorded (`evals/reports/live.json` is absent): live evals were "
        "not run. `make eval-live` runs them with `ANTHROPIC_API_KEY` set, within "
        "`LIVE_EVAL_BUDGET_USD`.",
        "",
        "## What the offline numbers mean",
        "",
        "Offline runs use FakeLLM, a deterministic stand-in that reads notes and replies with "
        "keyword rules. They measure the code that owns every decision with a side effect "
        "(priority score, compliance engine, STOP rule, approval and write-back guards, schema "
        "validation and PII redaction) end to end, and they catch regressions in it. They say "
        "nothing about how well a real model reads free text; see `docs/evaluation.md`. Open "
        "gaps are listed in `docs/KNOWN_LIMITATIONS.md`.",
        "",
    ]
    return "\n".join(lines)


async def run_all(
    names: list[str], *, mode: str, with_dev: bool, update_baseline: bool
) -> tuple[list[SuiteRun], bool]:
    settings = get_settings()
    baselines = load_baselines(BASELINE)
    runs: list[SuiteRun] = []
    for name in names:
        suite = engine.load_suite(SUITES_DIR / f"{name}.yaml").suite
        report = await run_one(name, GATED_SPLIT, mode, settings)
        _save(REPORTS_DIR / f"{name}.json", report)
        dev = await run_one(name, "dev", mode, settings) if with_dev else None
        if dev is not None:
            _save(REPORTS_DIR / f"{name}.dev.json", dev)
        base = baselines.get(name)
        runs.append(SuiteRun(name, report, gate(report, base, suite), dev, base))
    created = False
    # A run with errored cases never becomes the baseline. One that misses a threshold may: the
    # regression gate then still stops it getting worse while the gap is open.
    clean = not any(c.error for r in runs for c in r.report.cases)
    if mode == "offline" and clean and (update_baseline or not baselines):
        write_baselines(BASELINE, {**baselines, **{r.name: r.report for r in runs}})
        created = True
    return runs, created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=["offline"], default="offline")
    parser.add_argument("--suite", action="append", choices=SUITES, dest="suites")
    parser.add_argument("--no-dev", action="store_true", help="skip the informational dev run")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="replace the committed baseline with this run (review the diff before committing)",
    )
    args = parser.parse_args(argv)
    os.environ["CC_LLM_PROVIDER"] = "fake"  # offline means offline, whatever the shell has set
    get_settings.cache_clear()
    if os.environ.get("CI") and not BASELINE.exists() and not args.update_baseline:
        print(f"{BASELINE} is missing: CI gates against the committed baseline", file=sys.stderr)
        return 2
    names = args.suites or list(SUITES)
    runs, created = asyncio.run(
        run_all(
            names, mode=args.mode, with_dev=not args.no_dev, update_baseline=args.update_baseline
        )
    )
    from collections_copilot.evals import judge, live  # live imports this module

    tone = asyncio.run(judge.calibrate_tone(GATED_SPLIT))
    summary = live.load_summary()
    live_section = None
    if summary:
        live_section = live.render_summary(summary)
        current = prompt_hashes(sorted(prompt_names()))
        if summary.get("prompt_hashes") != current:
            live_section = (
                "**Stale:** the prompts changed since this live run; its numbers describe the "
                "earlier prompts.\n\n" + live_section
            )
    REPORT_MD.write_text(
        render(
            runs,
            live_section=live_section,
            extra_sections=[tone_section(tone, mode="offline")],
            baseline=load_baselines(BASELINE).get(runs[0].name) if runs else None,
        )
    )
    for run in runs:
        status = "PASS" if run.check.passed else "FAIL"
        print(f"{run.name}: {status} ({run.report.n_cases} {GATED_SPLIT} cases)")
        for f in run.check.failures:
            print(f"  FAIL {f.metric}: {f.reason} value={f.value} threshold={f.threshold}")
    if created:
        print(f"wrote baseline {BASELINE}")
    print(f"wrote {REPORT_MD}")
    return 0 if all(r.check.passed for r in runs) else 1


if __name__ == "__main__":
    sys.exit(main())

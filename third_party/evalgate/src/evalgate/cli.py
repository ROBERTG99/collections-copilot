"""`evalgate` CLI subset: run, check, report, calibrate. Exit codes follow upstream §4.4."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import typer

from evalgate.gate import check as run_check
from evalgate.gate import sha256_file
from evalgate.loader import InvalidInputError, load_suite
from evalgate.models import Report
from evalgate.report import render_markdown
from evalgate.runner import run_suite

app = typer.Typer(add_completion=False, help="evalgate (subset vendored by collections-copilot)")


def _load_report(path: Path) -> Report:
    return Report.model_validate_json(path.read_text())


@app.command()
def run(
    suite: Path,
    out: Path = typer.Option(..., "--out"),
    split: str | None = typer.Option(None, "--split"),
) -> None:
    """Run a suite and write a report.v1 JSON."""
    try:
        s = load_suite(suite)
        sys.path.insert(0, str(Path.cwd()))
        report = asyncio.run(run_suite(s, split=split))
    except InvalidInputError as exc:
        typer.echo(f"invalid input: {exc}", err=True)
        raise typer.Exit(2) from exc
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.model_dump_json(by_alias=True, indent=2))
    typer.echo(f"wrote {out} ({report.n_cases} cases)")


@app.command()
def check(
    report: Path,
    baseline: Path | None = typer.Option(None, "--baseline"),
    max_regression_pp: float = typer.Option(1.0, "--max-regression-pp"),
    suite: Path | None = typer.Option(None, "--suite"),
    out_json: Path | None = typer.Option(None, "--out-json"),
) -> None:
    """Gate a report on thresholds, budgets and regression against a baseline."""
    rep = _load_report(report)
    base = _load_report(baseline) if baseline and baseline.exists() else None
    s = load_suite(suite) if suite else None
    result = run_check(
        rep,
        sha256_file(report),
        base,
        sha256_file(baseline) if base and baseline else None,
        max_regression_pp,
        s,
    )
    if out_json:
        out_json.write_text(result.model_dump_json(by_alias=True, indent=2))
    for f in result.failures:
        typer.echo(f"FAIL {f.metric}: {f.reason} value={f.value} threshold={f.threshold}")
    typer.echo("PASS" if result.passed else "FAIL")
    raise typer.Exit(0 if result.passed else 1)


@app.command()
def report(
    reports: list[Path],
    md: Path = typer.Option(..., "--md"),
    baseline: Path | None = typer.Option(None, "--baseline"),
) -> None:
    """Render Markdown for one or more reports."""
    base = _load_report(baseline) if baseline and baseline.exists() else None
    items = []
    for p in reports:
        r = _load_report(p)
        items.append((r, run_check(r, sha256_file(p), base)))
    md.write_text(render_markdown(items))
    typer.echo(json.dumps({"written": str(md)}))


def main() -> None:
    app()


if __name__ == "__main__":
    main()

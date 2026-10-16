"""Markdown rendering of one or more reports, with optional baseline comparison."""

from __future__ import annotations

from evalgate.models import CheckResult, Report


def _fmt(v: float) -> str:
    return f"{v:.4f}" if abs(v) < 1000 else f"{v:,.0f}"


def render_markdown(
    reports: list[tuple[Report, CheckResult | None]], title: str = "Evaluation report"
) -> str:
    lines = [f"# {title}", ""]
    for report, chk in reports:
        status = "n/a" if chk is None else ("PASS" if chk.passed else "FAIL")
        lines += [
            f"## Suite `{report.suite}` — {status}",
            "",
            f"- Run `{report.run_id}` at {report.created_at.isoformat()} (mode **{report.mode}**)",
            f"- Commit `{report.git_sha or 'unknown'}`; cases {report.n_cases}",
            f"- Models: {', '.join(report.models) or 'none recorded'}",
            f"- Latency p50 {report.latency_p50_ms:.1f} ms, p95 {report.latency_p95_ms:.1f} ms; "
            f"cost USD {report.cost_usd_total:.4f} ({report.cost_usd_per_case:.6f} per case)",
        ]
        if report.prompt_hashes:
            hashes = ", ".join(f"`{k}` {v[:12]}" for k, v in sorted(report.prompt_hashes.items()))
            lines.append(f"- Prompt hashes: {hashes}")
        lines += [
            "",
            "| Metric | Value | Threshold | Direction | Gate | Passed |",
            "|---|---|---|---|---|---|",
        ]
        for name, mr in report.metrics.items():
            lines.append(
                f"| `{name}` | {_fmt(mr.value)} | {_fmt(mr.threshold)} | {mr.direction} | "
                f"{'yes' if mr.gate else 'no'} | {'yes' if mr.passed else 'NO'} |"
            )
        if chk and chk.comparisons:
            lines += [
                "",
                "| Metric | Baseline | Candidate | Δ pp | 95% CI pp | Paired |",
                "|---|---|---|---|---|---|",
            ]
            for c in chk.comparisons:
                lines.append(
                    f"| `{c.metric}` | {_fmt(c.baseline)} | {_fmt(c.candidate)} | "
                    f"{c.delta_pp:+.2f} | [{c.ci_low_pp:+.2f}, {c.ci_high_pp:+.2f}] | {c.n_paired} |"
                )
        if chk and chk.failures:
            lines += ["", "Failures:", ""]
            lines += [
                f"- `{f.metric}`: {f.reason} (value {f.value}, threshold {f.threshold})"
                for f in chk.failures
            ]
        failed = [c for c in report.cases if c.error][:5]
        if failed:
            lines += ["", "Errored cases (first 5):", ""]
            lines += [f"- `{c.id}`: {c.error}" for c in failed]
        lines.append("")
    return "\n".join(lines)

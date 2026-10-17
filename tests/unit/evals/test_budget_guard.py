"""T-41: `make eval-live` prints its estimate, stops at LIVE_EVAL_BUDGET_USD and needs no key."""

from __future__ import annotations

from typing import Any

import pytest
from evalgate.models import DatasetRow, MetricSpec, Suite, TargetResult, TargetSpec

from collections_copilot.evals import live
from collections_copilot.llm.providers.base import ProviderRequest, ProviderResponse, Usage


def _suite() -> Suite:
    return Suite.model_validate(
        {
            "schema": "evalgate.suite.v1",
            "name": "t",
            "dataset": "unused.jsonl",
            "target": TargetSpec(python="unused:target"),
            "metrics": [
                MetricSpec(
                    name="ok",
                    kind="exact",
                    params={"actual": "output.v", "expected": "expected.v"},
                    threshold=0.5,
                )
            ],
        }
    )


async def test_stops_at_budget() -> None:
    rows = [DatasetRow(id=f"r{i}", split="test", input={}, expected={"v": 1}) for i in range(10)]
    calls: list[int] = []

    async def target(case_input: dict[str, Any]) -> TargetResult:
        calls.append(1)
        return TargetResult(output={"v": 1}, cost_usd=0.01)

    guard = live.BudgetGuard(limit_usd=0.035)
    run = await live.run_budgeted(_suite(), rows, target, guard, per_case_estimate_usd=0.01)
    # 3 cases cost 0.03; a fourth would cross 0.035, so it never starts.
    assert len(calls) == 3
    assert run.stopped_at_budget
    assert run.report.n_cases == 3
    assert run.report.mode == "live"
    assert guard.spent_usd == pytest.approx(0.03)
    assert guard.spent_usd <= guard.limit_usd


async def test_observed_cost_overrides_a_low_estimate() -> None:
    rows = [DatasetRow(id=f"r{i}", split="test", input={}, expected={"v": 1}) for i in range(10)]

    async def target(case_input: dict[str, Any]) -> TargetResult:
        return TargetResult(output={"v": 1}, cost_usd=0.02)

    guard = live.BudgetGuard(limit_usd=0.05)
    run = await live.run_budgeted(_suite(), rows, target, guard, per_case_estimate_usd=0.001)
    assert run.report.n_cases == 2  # after the first case the real 0.02 per case is used
    assert guard.spent_usd <= guard.limit_usd


def test_without_a_key_nothing_runs_and_exit_is_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("no live run without a key")

    monkeypatch.setattr(live, "run_live", boom)
    assert live.main([]) == 0
    assert "Live evals were not run" in capsys.readouterr().out


def test_estimate_is_printed_and_a_zero_budget_runs_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("LIVE_EVAL_BUDGET_USD", "0")

    def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a zero budget must not start a run")

    monkeypatch.setattr(live, "run_live", boom)
    assert live.main([]) == 0
    out = capsys.readouterr().out
    assert "Estimated cost" in out
    assert "exceeds LIVE_EVAL_BUDGET_USD" in out


async def test_meter_and_recorder_wrap_the_provider(tmp_path: Any) -> None:
    class Inner:
        name = "inner"

        async def complete(self, request: ProviderRequest) -> ProviderResponse:
            return ProviderResponse(
                '{"label": 1, "reason": "ok"}',
                "end_turn",
                request.model,
                Usage(input_tokens=1_000_000, output_tokens=0),
            )

    meter = live.Meter()
    wrapped = live.wrap_provider(Inner(), meter=meter, record_dir=tmp_path)
    request = ProviderRequest("judge_tone", "claude-haiku-5-5", "system", "user", {}, "low", 64)
    await wrapped.complete(request)
    assert meter.cost_usd > 0
    saved = list((tmp_path / "judge_tone").glob("*.json"))
    assert len(saved) == 1

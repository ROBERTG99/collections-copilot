"""Runs a suite against a Python callable or a LangGraph graph and builds a v1 report."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import os
import subprocess
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evalgate import metrics as m
from evalgate.loader import load_dataset
from evalgate.models import (
    CaseResult,
    DatasetRow,
    MetricResult,
    Report,
    StepRecord,
    Suite,
    TargetResult,
)

Target = Callable[[dict[str, Any]], Awaitable[TargetResult]]


def _import(ref: str) -> Any:
    module, _, attr = ref.partition(":")
    obj: Any = importlib.import_module(module)
    for part in attr.split("."):
        obj = getattr(obj, part)
    return obj


def _coerce(value: Any) -> TargetResult:
    if isinstance(value, TargetResult):
        return value
    if (
        isinstance(value, dict)
        and "output" in value
        and set(value) <= set(TargetResult.model_fields)
    ):
        return TargetResult.model_validate(value)
    return TargetResult(output=value)


def python_target(ref: str) -> Target:
    fn = _import(ref)

    async def call(case_input: dict[str, Any]) -> TargetResult:
        if inspect.iscoroutinefunction(fn):
            return _coerce(await fn(case_input))
        return _coerce(await asyncio.to_thread(fn, case_input))

    return call


def langgraph_target(ref: str) -> Target:
    """Streams node updates; one StepRecord per node. The graph may be a factory callable."""
    obj = _import(ref)

    async def call(case_input: dict[str, Any]) -> TargetResult:
        graph = obj() if callable(obj) and not hasattr(obj, "astream") else obj
        if inspect.isawaitable(graph):
            graph = await graph
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}
        state: dict[str, Any] = dict(case_input)
        steps: list[StepRecord] = []
        t_prev = time.perf_counter()
        async for update in graph.astream(case_input, config, stream_mode="updates"):
            now = time.perf_counter()
            for node, delta in update.items():
                if node == "__interrupt__":
                    state["__interrupt__"] = True
                    continue
                steps.append(
                    StepRecord(node=node, step=len(steps), latency_ms=(now - t_prev) * 1000)
                )
                if isinstance(delta, dict):
                    state.update(delta)
            t_prev = now
        extra = state.pop("_steps", [])
        steps.extend(StepRecord.model_validate(s) for s in extra)
        return TargetResult(output=state, steps=steps, cost_usd=sum(s.cost_usd for s in steps))

    return call


def _git_sha() -> str | None:
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "HEAD"],  # noqa: S607
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            or None
        )
    except (OSError, subprocess.CalledProcessError):
        return None


async def run_suite(
    suite: Suite,
    split: str | None = None,
    base_dir: Path | None = None,
    mode: str = "offline",
) -> Report:
    base = base_dir or Path.cwd()
    rows = load_dataset(base / suite.dataset, split)
    target = (
        python_target(suite.target.python)
        if suite.target.python
        else langgraph_target(suite.target.langgraph or "")
    )
    sem = asyncio.Semaphore(suite.concurrency)

    async def one(row: DatasetRow) -> tuple[DatasetRow, TargetResult, str | None]:
        async with sem:
            t0 = time.perf_counter()
            try:
                res = await asyncio.wait_for(target(row.input), suite.timeout_s)
                err = None
            except Exception as exc:  # a failing case never aborts a run
                res, err = TargetResult(output={}), f"{type(exc).__name__}: {exc}"
            res.latency_ms = res.latency_ms or (time.perf_counter() - t0) * 1000
            return row, res, err

    results = await asyncio.gather(*(one(r) for r in rows))
    return build_report(suite, results, mode=mode)


def build_report(
    suite: Suite,
    results: list[tuple[DatasetRow, TargetResult, str | None]],
    mode: str = "offline",
) -> Report:
    per_case: dict[str, dict[str, float]] = {row.id: {} for row, _, _ in results}
    metric_results: dict[str, MetricResult] = {}
    for spec in suite.metrics:
        name = spec.params.get("name", spec.name)
        if spec.kind == "custom" and m.is_corpus(name):
            fn: Any = m.registered(name)
            value = float(fn([(r, res) for r, res, _ in results], spec.params))
        else:
            values: list[float] = []
            for row, res, err in results:
                v = 0.0 if err and spec.direction == "higher" else m.case_value(spec, row, res)
                if v is None:
                    continue
                per_case[row.id][spec.name] = float(v)
                values.append(float(v))
            value = m.aggregate(values, spec.aggregate)
        metric_results[spec.name] = MetricResult(
            value=round(value, 6),
            threshold=spec.threshold,
            direction=spec.direction,
            passed=m.passes(value, spec),
            gate=spec.gate,
        )
    latencies = [res.latency_ms for _, res, _ in results]
    cost = sum(res.cost_usd for _, res, _ in results)
    models = sorted({s.model for _, res, _ in results for s in res.steps if s.model})
    cases = [
        CaseResult(
            id=row.id,
            split=row.split,
            scores=per_case[row.id],
            passed=err is None,
            cost_usd=res.cost_usd,
            latency_ms=round(res.latency_ms, 3),
            error=err,
        )
        for row, res, err in results
    ]
    n = len(results)
    return Report(
        suite=suite.name,
        run_id=str(uuid.uuid4()),
        created_at=datetime.now(UTC),
        git_sha=os.environ.get("GITHUB_SHA") or _git_sha(),
        mode="live" if mode == "live" else "offline",
        models=models,
        n_cases=n,
        metrics=metric_results,
        cost_usd_total=round(cost, 6),
        cost_usd_per_case=round(cost / n, 6) if n else 0.0,
        latency_p50_ms=round(m.percentile(latencies, 50), 3),
        latency_p95_ms=round(m.percentile(latencies, 95), 3),
        cases=cases,
    )

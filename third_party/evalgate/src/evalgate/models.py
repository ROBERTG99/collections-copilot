"""Pydantic models for the evalgate v0.1.0 contracts used by this repository.

Subset of the upstream contracts (suite v1, dataset row, report v1, check v1, rubric v1).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StepRecord(BaseModel):
    """The shared step record (one per graph node, model call or tool call)."""

    run_id: str = ""
    task_id: str = ""
    step: int = 0
    node: str
    kind: Literal["llm", "tool", "node", "rule"] = "node"
    model: str | None = None
    prompt_version: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    status: Literal["ok", "error", "fallback"] = "ok"
    error: str | None = None
    started_at: datetime | None = None


class TargetResult(BaseModel):
    output: dict[str, Any] | str
    steps: list[StepRecord] = Field(default_factory=list)
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    trace_id: str | None = None


class TargetSpec(_Strict):
    python: str | None = None
    langgraph: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> TargetSpec:
        if (self.python is None) == (self.langgraph is None):
            raise ValueError("target takes exactly one of python or langgraph")
        return self


class MetricSpec(_Strict):
    name: str
    kind: Literal["exact", "json_schema", "numeric_tolerance", "contains_all", "regex", "custom"]
    params: dict[str, Any] = Field(default_factory=dict)
    threshold: float
    direction: Literal["higher", "lower"] = "higher"
    gate: bool = True
    aggregate: Literal["mean", "sum", "max", "p95"] = "mean"


class Budget(_Strict):
    max_cost_usd: float | None = None
    max_p95_ms: float | None = None


class Suite(_Strict):
    schema_: Literal["evalgate.suite.v1"] = Field(alias="schema")
    name: str
    dataset: str
    target: TargetSpec
    metrics: list[MetricSpec]
    concurrency: int = 8
    timeout_s: float = 30.0
    budget: Budget | None = None
    description: str | None = None
    prompts: list[str] = Field(default_factory=list)


class DatasetRow(_Strict):
    id: str
    split: Literal["dev", "test"]
    input: dict[str, Any]
    expected: dict[str, Any] | None
    tags: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class MetricResult(BaseModel):
    value: float
    threshold: float
    direction: Literal["higher", "lower"]
    passed: bool
    gate: bool = True
    ci_low: float | None = None
    ci_high: float | None = None


class CaseResult(BaseModel):
    id: str
    split: str
    scores: dict[str, float]
    passed: bool
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    error: str | None = None


class Report(BaseModel):
    schema_: Literal["evalgate.report.v1"] = Field(default="evalgate.report.v1", alias="schema")
    suite: str
    run_id: str
    created_at: datetime
    git_sha: str | None = None
    mode: Literal["offline", "live"] = "offline"
    models: list[str] = Field(default_factory=list)
    prompt_hashes: dict[str, str] = Field(default_factory=dict)
    n_cases: int
    metrics: dict[str, MetricResult]
    cost_usd_total: float = 0.0
    cost_usd_per_case: float = 0.0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    cases: list[CaseResult] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)


class Failure(BaseModel):
    metric: str
    reason: Literal["threshold", "regression", "budget_cost", "budget_p95", "error"]
    value: float | None = None
    threshold: float | None = None
    delta_pp: float | None = None
    ci_low_pp: float | None = None
    ci_high_pp: float | None = None


class Comparison(BaseModel):
    metric: str
    baseline: float
    candidate: float
    delta_pp: float
    ci_low_pp: float
    ci_high_pp: float
    n_paired: int


class CheckResult(BaseModel):
    schema_: Literal["evalgate.check.v1"] = Field(default="evalgate.check.v1", alias="schema")
    report_sha256: str
    baseline_sha256: str | None = None
    max_regression_pp: float
    seed: int
    resamples: int
    passed: bool
    failures: list[Failure] = Field(default_factory=list)
    comparisons: list[Comparison] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class RubricThresholds(_Strict):
    agreement_min: float = 0.8
    kappa_min: float = 0.7
    position_inconsistency_max: float = 0.10
    verbosity_bias_max: float = 0.05


class Rubric(_Strict):
    schema_: Literal["evalgate.rubric.v1"] = Field(alias="schema")
    name: str
    version: str
    mode: Literal["pointwise", "pairwise"] = "pointwise"
    scale: Literal["binary", "likert5"] = "binary"
    prompt: str
    route: str
    criteria: list[str]
    thresholds: RubricThresholds = Field(default_factory=RubricThresholds)
    position_swap: bool = False
    verbosity_probe: bool = False


class LabelRow(_Strict):
    id: str
    rubric: str
    split: Literal["dev", "test"]
    input: dict[str, Any]
    reference: str | None = None
    candidate: str
    candidate_b: str | None = None
    label: int | str
    source: Literal["construction", "maintainer"] = "construction"
    meta: dict[str, Any] = Field(default_factory=dict)

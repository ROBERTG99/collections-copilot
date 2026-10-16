"""Deterministic metrics and the custom-metric registry.

Per-case metrics return a float in [0, 1] (or a count) or ``None`` when the case does not apply.
Corpus metrics (``level="corpus"``) receive every case and return one value (for example macro-F1).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from evalgate.models import DatasetRow, MetricSpec, TargetResult


@dataclass(frozen=True)
class MetricScore:
    name: str
    value: float | None
    passed: bool
    details: dict[str, Any]


CaseMetricFn = Callable[[DatasetRow, TargetResult, dict[str, Any]], float | None]
CorpusMetricFn = Callable[[Sequence[tuple[DatasetRow, TargetResult]], dict[str, Any]], float]

_CASE: dict[str, CaseMetricFn] = {}
_CORPUS: dict[str, CorpusMetricFn] = {}


def metric(name: str, level: str = "case") -> Callable[[Any], Any]:
    """Register a custom metric: ``@evalgate.metric("name")``."""

    def deco(fn: Any) -> Any:
        if level == "corpus":
            _CORPUS[name] = fn
        else:
            _CASE[name] = fn
        return fn

    return deco


def registered(name: str) -> CaseMetricFn | CorpusMetricFn | None:
    return _CASE.get(name) or _CORPUS.get(name)


def is_corpus(name: str) -> bool:
    return name in _CORPUS


def select(path: str, row: DatasetRow, result: TargetResult) -> Any:
    """Resolve a dotted selector rooted at input, output, expected or meta."""
    root, _, rest = path.partition(".")
    roots: dict[str, Any] = {
        "input": row.input,
        "output": result.output,
        "expected": row.expected or {},
        "meta": row.meta,
    }
    if root not in roots:
        raise KeyError(f"selector must start with input/output/expected/meta: {path}")
    cur: Any = roots[root]
    for part in [p for p in rest.split(".") if p]:
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)] if int(part) < len(cur) else None
        else:
            return None
    return cur


def _exact(row: DatasetRow, res: TargetResult, p: dict[str, Any]) -> float | None:
    expected = select(p["expected"], row, res)
    if expected is None and p.get("skip_missing", False):
        return None
    return 1.0 if select(p["actual"], row, res) == expected else 0.0


def _numeric(row: DatasetRow, res: TargetResult, p: dict[str, Any]) -> float | None:
    a, e = select(p["actual"], row, res), select(p["expected"], row, res)
    if e is None:
        return None
    if a is None:
        return 0.0
    return 1.0 if abs(float(a) - float(e)) <= float(p.get("tolerance", 0)) else 0.0


def _contains_all(row: DatasetRow, res: TargetResult, p: dict[str, Any]) -> float | None:
    text = str(select(p["actual"], row, res) or "")
    needles = select(p["expected"], row, res) if isinstance(p["expected"], str) else p["expected"]
    if not needles:
        return None
    return 1.0 if all(str(n) in text for n in needles) else 0.0


def _regex(row: DatasetRow, res: TargetResult, p: dict[str, Any]) -> float | None:
    text = str(select(p["actual"], row, res) or "")
    return 1.0 if re.search(p["pattern"], text) else 0.0


def _json_schema(row: DatasetRow, res: TargetResult, p: dict[str, Any]) -> float | None:
    """Structural validity: required keys present and enum values respected."""
    value = select(p.get("actual", "output"), row, res)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return 0.0
    if not isinstance(value, dict):
        return 0.0
    schema = p.get("schema", {})
    for key in schema.get("required", []):
        if key not in value:
            return 0.0
    for key, spec in schema.get("properties", {}).items():
        if key in value and "enum" in spec and value[key] not in spec["enum"]:
            return 0.0
    return 1.0


BUILTIN: dict[str, CaseMetricFn] = {
    "exact": _exact,
    "numeric_tolerance": _numeric,
    "contains_all": _contains_all,
    "regex": _regex,
    "json_schema": _json_schema,
}


def aggregate(values: list[float], how: str) -> float:
    if not values:
        return 0.0
    if how == "sum":
        return float(sum(values))
    if how == "max":
        return float(max(values))
    if how == "p95":
        return percentile(values, 95)
    return float(sum(values) / len(values))


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return float(ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo))


def case_value(spec: MetricSpec, row: DatasetRow, res: TargetResult) -> float | None:
    if spec.kind == "custom":
        fn = _CASE.get(spec.params.get("name", spec.name))
        if fn is None:
            raise KeyError(f"custom metric {spec.name!r} is not registered")
        return fn(row, res, spec.params)
    return BUILTIN[spec.kind](row, res, spec.params)


def passes(value: float, spec: MetricSpec) -> bool:
    return value >= spec.threshold if spec.direction == "higher" else value <= spec.threshold

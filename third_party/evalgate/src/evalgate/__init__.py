"""Subset of evalgate v0.1.0 contracts used by collections-copilot (fallback; see docs/DECISIONS.md)."""

from evalgate.metrics import MetricScore, metric
from evalgate.models import DatasetRow as Case
from evalgate.models import Report, StepRecord, Suite, TargetResult

__version__ = "0.1.0"
__all__ = ["Case", "MetricScore", "Report", "StepRecord", "Suite", "TargetResult", "metric"]

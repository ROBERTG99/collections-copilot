"""Judge calibration: agreement and Cohen's kappa between a judge and labelled items."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from evalgate.models import LabelRow, Rubric


@dataclass
class CalibrationResult:
    n: int
    agreement: float
    kappa: float
    confusion: dict[str, int] = field(default_factory=dict)
    passed: bool = False


def cohen_kappa(a: Sequence[object], b: Sequence[object]) -> float:
    n = len(a)
    if n == 0:
        return 0.0
    po = sum(1 for x, y in zip(a, b, strict=True) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb.get(k, 0) for k in ca) / (n * n)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def calibrate(
    rubric: Rubric, labels: Sequence[LabelRow], judge: Callable[[LabelRow], int | str]
) -> CalibrationResult:
    predicted = [judge(row) for row in labels]
    gold = [row.label for row in labels]
    n = len(gold)
    agreement = sum(1 for p, g in zip(predicted, gold, strict=True) if p == g) / n if n else 0.0
    kappa = cohen_kappa(gold, predicted)
    confusion = Counter(f"{g}->{p}" for g, p in zip(gold, predicted, strict=True))
    t = rubric.thresholds
    return CalibrationResult(
        n=n,
        agreement=round(agreement, 4),
        kappa=round(kappa, 4),
        confusion=dict(confusion),
        passed=agreement >= t.agreement_min and kappa >= t.kappa_min,
    )

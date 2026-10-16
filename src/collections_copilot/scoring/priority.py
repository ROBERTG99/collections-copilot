"""Deterministic priority score 0–100 (FR-2). The LLM never changes it.

score = round(balance + days_past_due + broken_promises + segment), where
  balance          = 40 × min(1, log10(1 + balance_usd) / log10(1 + 50_000))
  days_past_due    = 35 × min(1, max_dpd / 120)
  broken_promises  = 15 × min(3, broken_in_90_days) / 3
  segment          = 10 (enterprise) | 6 (mid_market) | 3 (smb)
Balances in other currencies use the fixed scoring rates in SCORING_USD_RATES (scoring only).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import NamedTuple

FORMULA_VERSION = "priority.v1"
BALANCE_POINTS = 40.0
DPD_POINTS = 35.0
BROKEN_POINTS = 15.0
BALANCE_CAP_USD = 50_000.0
DPD_CAP_DAYS = 120.0
SEGMENT_POINTS = {"enterprise": 10.0, "mid_market": 6.0, "smb": 3.0}
SCORING_USD_RATES = {"USD": 1.0, "EUR": 1.08}


class PriorityInputs(NamedTuple):
    balance_cents: int
    currency: str
    max_days_past_due: int
    broken_promises_90d: int
    segment: str


@dataclass(frozen=True)
class PriorityScore:
    score: int
    components: dict[str, float] = field(default_factory=dict)
    version: str = FORMULA_VERSION


def score(x: PriorityInputs) -> PriorityScore:
    usd = max(0, x.balance_cents) / 100 * SCORING_USD_RATES.get(x.currency, 1.0)
    balance = BALANCE_POINTS * min(1.0, math.log10(1 + usd) / math.log10(1 + BALANCE_CAP_USD))
    dpd = DPD_POINTS * min(1.0, max(0, x.max_days_past_due) / DPD_CAP_DAYS)
    broken = BROKEN_POINTS * min(3, max(0, x.broken_promises_90d)) / 3
    segment = SEGMENT_POINTS.get(x.segment, SEGMENT_POINTS["smb"])
    components = {
        "balance": round(balance, 2),
        "days_past_due": round(dpd, 2),
        "broken_promises": round(broken, 2),
        "segment": segment,
    }
    total = math.floor(balance + dpd + broken + segment + 0.5)
    return PriorityScore(score=max(0, min(100, total)), components=components)


def inputs_from(
    *,
    invoices: list[tuple[int, str, int]],
    broken_promises_90d: int,
    segment: str,
    currency: str,
) -> PriorityInputs:
    """Build inputs from (amount_cents, status, days_past_due) tuples; only open invoices count."""
    open_invoices = [(a, d) for a, s, d in invoices if s == "open"]
    return PriorityInputs(
        balance_cents=sum(a for a, _ in open_invoices),
        currency=currency,
        max_days_past_due=max((d for _, d in open_invoices), default=0),
        broken_promises_90d=broken_promises_90d,
        segment=segment,
    )

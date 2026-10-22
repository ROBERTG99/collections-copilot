"""The priority score is deterministic, bounded and monotonic (FR-2)."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from collections_copilot.scoring.priority import PriorityInputs, score

BASE = PriorityInputs(
    balance_cents=250_000,
    currency="USD",
    max_days_past_due=45,
    broken_promises_90d=0,
    segment="mid_market",
)

balances = st.integers(min_value=1, max_value=10_000_000_000)
dpds = st.integers(min_value=0, max_value=1_000)
segments = st.sampled_from(["smb", "mid_market", "enterprise"])
broken = st.integers(min_value=0, max_value=10)


def test_monotonic_in_balance_and_dpd() -> None:
    small = score(BASE._replace(balance_cents=100_000))
    large = score(BASE._replace(balance_cents=1_000_000))
    assert large.score > small.score
    young = score(BASE._replace(max_days_past_due=10))
    old = score(BASE._replace(max_days_past_due=90))
    assert old.score > young.score
    assert score(BASE._replace(broken_promises_90d=2)).score > score(BASE).score
    assert (
        score(BASE._replace(segment="enterprise")).score > score(BASE._replace(segment="smb")).score
    )


@given(a=balances, b=balances, dpd=dpds, seg=segments, br=broken)
def test_balance_property(a: int, b: int, dpd: int, seg: str, br: int) -> None:
    lo, hi = sorted((a, b))
    x = PriorityInputs(lo, "USD", dpd, br, seg)
    assert score(x._replace(balance_cents=hi)).score >= score(x).score


@given(a=dpds, b=dpds, bal=balances, seg=segments, br=broken)
def test_dpd_property(a: int, b: int, bal: int, seg: str, br: int) -> None:
    lo, hi = sorted((a, b))
    x = PriorityInputs(bal, "USD", lo, br, seg)
    assert score(x._replace(max_days_past_due=hi)).score >= score(x).score


@given(bal=balances, dpd=dpds, seg=segments, br=broken)
def test_bounded_and_deterministic(bal: int, dpd: int, seg: str, br: int) -> None:
    x = PriorityInputs(bal, "EUR", dpd, br, seg)
    s = score(x)
    assert 0 <= s.score <= 100
    assert s == score(x)
    assert set(s.components) == {"balance", "days_past_due", "broken_promises", "segment"}


def test_zero_balance_scores_zero_balance_component() -> None:
    s = score(BASE._replace(balance_cents=0, max_days_past_due=0))
    assert s.components["balance"] == 0
    assert s.components["days_past_due"] == 0

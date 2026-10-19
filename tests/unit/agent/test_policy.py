"""FR-6 / NFR-5: auto-approval is narrow and fails closed."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from collections_copilot.agent import policy
from collections_copilot.compliance import engine

ENV_MAX, ENV_MIN = 500.0, 0.9
DEFAULT = policy.effective(None, ENV_MAX, ENV_MIN)


def check(**overrides: Any) -> tuple[bool, str]:
    args: dict[str, Any] = {
        "action": "remind",
        "open_balance_cents": 30_000,
        "currency": "USD",
        "confidence": 0.95,
        "postcheck_ok": True,
        "requires_edit": False,
        "invoice_currencies": ["USD"],
        "rules_action": "remind",
    }
    pol = overrides.pop("policy", DEFAULT)
    args.update(overrides)
    return policy.qualifies(pol, **args)


def test_small_confident_clean_remind_qualifies() -> None:
    assert check()[0]


@pytest.mark.parametrize(
    "overrides",
    [
        {"action": "payment_plan", "confidence": 0.99},
        {"action": "escalate"},
        {"open_balance_cents": 50_001},
        {"confidence": 0.89},
        {"postcheck_ok": False},
        {"requires_edit": True},
        {"currency": "GBP", "invoice_currencies": ["GBP"], "open_balance_cents": 45_000},
        {"currency": "JPY", "invoice_currencies": ["JPY"], "open_balance_cents": 100},
        {"invoice_currencies": ["USD", "EUR"]},
        {"policy": policy.effective({"enabled": False}, ENV_MAX, ENV_MIN)},
        {"policy": policy.effective({"max_usd": 100}, ENV_MAX, ENV_MIN)},
        {"policy": policy.effective({"min_confidence": 0.99}, ENV_MAX, ENV_MIN)},
        # M6 security audit M1: a note cannot talk the model into a reminder the house rules
        # would not choose for the same facts, however confident it says it is.
        {"rules_action": "escalate", "confidence": 0.99},
        {"rules_action": "pause", "confidence": 0.99},
    ],
)
def test_needs_a_human(overrides: dict[str, Any]) -> None:
    ok, reason = check(**overrides)
    assert not ok
    assert reason


def test_eur_converts_at_the_scoring_rate() -> None:
    # EUR 460 ≈ USD 497 qualifies; EUR 470 ≈ USD 508 does not.
    assert check(currency="EUR", invoice_currencies=["EUR"], open_balance_cents=46_000)[0]
    assert not check(currency="EUR", invoice_currencies=["EUR"], open_balance_cents=47_000)[0]


@pytest.mark.parametrize(
    "setting",
    [
        {"max_usd": 10_000, "min_confidence": 0.1},  # cannot loosen the environment ceiling
        {"max_usd": None, "min_confidence": None},  # nulls fall back to the environment
        {"max_usd": "lots"},  # malformed values never loosen
        {},
    ],
)
def test_tenant_setting_only_tightens(setting: dict[str, Any]) -> None:
    eff = policy.effective(setting, ENV_MAX, ENV_MIN)
    assert eff.max_usd <= ENV_MAX
    assert eff.min_confidence >= ENV_MIN
    assert eff.enabled


def test_contact_flag_blocks_precheck() -> None:
    ctx = engine.ComplianceContext(
        now=datetime(2026, 10, 16, 15, tzinfo=UTC),
        account=engine.AccountFacts("America/New_York", "en", "USD", False),
        invoices=[engine.InvoiceFacts("INV-1", 10_000, "USD", "open")],
        contact_flag="wrong_number",
    )
    result = engine.precheck(ctx)
    assert not result.ok
    assert [v.code for v in result.violations if v.blocking] == ["contact_flagged"]


@pytest.mark.parametrize(
    ("setting", "env_max", "env_min", "balance_cents", "confidence"),
    [
        (None, float("nan"), ENV_MIN, 10_000_000, 0.95),  # only the amount ceiling is broken
        (None, ENV_MAX, float("nan"), 30_000, 0.01),  # only the confidence floor is broken
        ({"max_usd": "nan"}, float("nan"), ENV_MIN, 10_000_000, 0.95),
        ({"max_usd": float("inf")}, float("inf"), ENV_MIN, 10_000_000, 0.95),
        ({"min_confidence": "-inf"}, ENV_MAX, float("-inf"), 30_000, 0.01),
    ],
)
def test_non_finite_values_fail_closed(
    setting: dict[str, Any] | None,
    env_max: float,
    env_min: float,
    balance_cents: int,
    confidence: float,
) -> None:
    """T-51 compliance review: min(nan, x) is nan, which every comparison then waves through."""
    eff = policy.effective(setting, env_max, env_min)
    ok, _ = check(policy=eff, open_balance_cents=balance_cents, confidence=confidence)
    assert not ok
    assert not eff.enabled


def test_the_ceiling_is_compared_in_exact_cents() -> None:
    """T-51 review m1: EUR 1.75 at 1.08 is exactly USD 1.89; in floats 1.75 * 1.08 is
    1.8900000000000001, which is "above" a 1.89 ceiling. Money is compared as Decimal cents."""
    tight = policy.EffectivePolicy(enabled=True, max_usd=1.89, min_confidence=0.9)
    eur = {"currency": "EUR", "invoice_currencies": ["EUR"]}
    assert check(policy=tight, open_balance_cents=175, **eur)[0]
    assert not check(policy=tight, open_balance_cents=176, **eur)[0]

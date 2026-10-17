"""Auto-approval policy (FR-6): only `remind`, amount ≤ max, confidence ≥ min, clean post-check.

Environment values (CC_AUTO_APPROVE_MAX_USD, CC_AUTO_APPROVE_MIN_CONFIDENCE) are the ceiling;
a supervisor's tenant setting can only tighten them (lower max, higher min, or disable).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from collections_copilot.scoring.priority import SCORING_USD_RATES


@dataclass(frozen=True)
class EffectivePolicy:
    enabled: bool
    max_usd: float
    min_confidence: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_usd": self.max_usd,
            "min_confidence": self.min_confidence,
        }


def effective(
    setting: dict[str, Any] | None, env_max_usd: float, env_min_confidence: float
) -> EffectivePolicy:
    s = {k: v for k, v in (setting or {}).items() if v is not None}
    return EffectivePolicy(
        enabled=s.get("enabled", True) is not False,
        max_usd=min(env_max_usd, _number(s.get("max_usd"), env_max_usd)),
        min_confidence=max(
            env_min_confidence, _number(s.get("min_confidence"), env_min_confidence)
        ),
    )


def _number(value: Any, default: float) -> float:
    """A malformed tenant value falls back to the environment ceiling (never loosens it)."""
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def is_tightening(new: dict[str, Any], env_max_usd: float, env_min_confidence: float) -> bool:
    return (
        float(new.get("max_usd", env_max_usd)) <= env_max_usd
        and float(new.get("min_confidence", env_min_confidence)) >= env_min_confidence
    )


def qualifies(
    policy: EffectivePolicy,
    *,
    action: str,
    open_balance_cents: int,
    currency: str,
    confidence: float,
    postcheck_ok: bool,
    requires_edit: bool,
    rules_action: str,
    invoice_currencies: Iterable[str] = (),
) -> tuple[bool, str]:
    """`confidence` is the model's own; the model reads untrusted notes, so the deterministic
    house rules must independently choose the same reminder (`rules_action`)."""
    if not policy.enabled:
        return False, "auto-approval disabled"
    if action != "remind":
        return False, f"action {action} always needs a human"
    if rules_action != "remind":
        return False, f"the house rules choose {rules_action}, not a reminder"
    if not postcheck_ok or requires_edit:
        return False, "post-check not clean"
    # Fail closed: an amount we cannot convert, or invoices in another currency, need a human.
    if currency not in SCORING_USD_RATES:
        return False, f"currency {currency} not convertible"
    if any(c != currency for c in invoice_currencies):
        return False, "invoices in more than one currency"
    usd = open_balance_cents / 100 * SCORING_USD_RATES[currency]
    if usd > policy.max_usd:
        return False, f"amount USD {usd:.2f} above {policy.max_usd:.2f}"
    if confidence < policy.min_confidence:
        return False, f"confidence {confidence:.2f} below {policy.min_confidence:.2f}"
    return True, "matches auto-approval policy"

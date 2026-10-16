"""Auto-approval policy (FR-6): only `remind`, amount ≤ max, confidence ≥ min, clean post-check.

Environment values (CC_AUTO_APPROVE_MAX_USD, CC_AUTO_APPROVE_MIN_CONFIDENCE) are the ceiling;
a supervisor's tenant setting can only tighten them (lower max, higher min, or disable).
"""

from __future__ import annotations

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
    s = setting or {}
    return EffectivePolicy(
        enabled=bool(s.get("enabled", True)),
        max_usd=min(env_max_usd, float(s.get("max_usd", env_max_usd))),
        min_confidence=max(env_min_confidence, float(s.get("min_confidence", env_min_confidence))),
    )


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
) -> tuple[bool, str]:
    if not policy.enabled:
        return False, "auto-approval disabled"
    if action != "remind":
        return False, f"action {action} always needs a human"
    if not postcheck_ok or requires_edit:
        return False, "post-check not clean"
    usd = open_balance_cents / 100 * SCORING_USD_RATES.get(currency, 1.0)
    if usd > policy.max_usd:
        return False, f"amount USD {usd:.2f} above {policy.max_usd:.2f}"
    if confidence < policy.min_confidence:
        return False, f"confidence {confidence:.2f} below {policy.min_confidence:.2f}"
    return True, "matches auto-approval policy"

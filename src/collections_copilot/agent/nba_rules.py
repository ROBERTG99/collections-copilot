"""Deterministic next-best-action rules: used when an account has no notes, and as the fallback
when the model is unavailable or refuses. Labels in nba.v1 for note-less accounts are
produced by these rules by construction."""

from __future__ import annotations

from dataclasses import dataclass

from collections_copilot.agent.schemas import NbaAction


@dataclass(frozen=True)
class RuleFacts:
    open_balance_cents: int
    max_days_past_due: int
    broken_promises_90d: int
    open_invoices: int
    disputed_invoices: int
    currency: str = "USD"


@dataclass(frozen=True)
class RuleDecision:
    action: NbaAction
    rationale: str
    confidence: float


ESCALATE_DPD = 120
ESCALATE_BROKEN = 2
PLAN_BALANCE_CENTS = 1_000_000
PLAN_DPD = 90


def decide(f: RuleFacts) -> RuleDecision:
    if f.open_invoices == 0 and f.disputed_invoices > 0:
        return RuleDecision("pause", "Every unpaid invoice is disputed.", 0.95)
    if f.open_invoices == 0 or f.open_balance_cents <= 0:
        return RuleDecision("no_action", "No open balance.", 0.95)
    if f.max_days_past_due >= ESCALATE_DPD and f.broken_promises_90d >= ESCALATE_BROKEN:
        return RuleDecision("escalate", "Long overdue with repeated broken promises.", 0.7)
    if f.open_balance_cents >= PLAN_BALANCE_CENTS and f.max_days_past_due >= PLAN_DPD:
        return RuleDecision("payment_plan", "Large balance overdue for 90+ days.", 0.65)
    return RuleDecision("remind", "Open balance past due; a reminder is appropriate.", 0.7)

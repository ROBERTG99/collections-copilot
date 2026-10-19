"""next_best_action node logic (FR-3): rules when there are no notes, otherwise the LLM with
validated output; invalid output twice routes the case to an analyst; provider failure or
refusal falls back to the rules, labelled `fallback` (NFR-11)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from collections_copilot.agent.nba_rules import RuleFacts, decide
from collections_copilot.agent.prompts import UntrustedNote, render
from collections_copilot.agent.schemas import NbaAction, NextBestAction
from collections_copilot.llm.gateway import LLMGateway, RouteSpec
from collections_copilot.llm.redaction import AccountPII


@dataclass(frozen=True)
class NbaInput:
    facts: RuleFacts
    segment: str
    language: str
    notes: list[UntrustedNote]
    pii: AccountPII | None = None


@dataclass(frozen=True)
class NbaDecision:
    action: NbaAction
    rationale: str
    confidence: float
    cited_note_ids: list[str] = field(default_factory=list)
    source: Literal["llm", "rules", "fallback"] = "rules"
    requires_analyst: bool = False
    analyst_reason: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    cost_usd: float = 0.0

    @property
    def fallback(self) -> bool:
        return self.source == "fallback"


def _context(inp: NbaInput) -> dict[str, object]:
    f = inp.facts
    return {
        "segment": inp.segment,
        "language": inp.language,
        "currency": f.currency,
        "open_balance_cents": f.open_balance_cents,
        "max_days_past_due": f.max_days_past_due,
        "broken_promises_90d": f.broken_promises_90d,
        "open_invoices": f.open_invoices,
        "disputed_invoices": f.disputed_invoices,
    }


async def decide_nba(
    gateway: LLMGateway, routes: dict[str, RouteSpec], inp: NbaInput
) -> NbaDecision:
    rules = decide(inp.facts)
    if not inp.notes:
        return NbaDecision(rules.action, rules.rationale, rules.confidence, source="rules")
    rendered = render("next_best_action", _context(inp), inp.notes)
    known = {n.id for n in inp.notes}

    def validator(out: NextBestAction) -> str | None:
        unknown = [i for i in out.cited_note_ids if i not in known]
        return f"cited_note_ids not in the notes: {unknown}" if unknown else None

    result = await gateway.structured(
        routes["next_best_action"],
        system=rendered.system,
        user=rendered.user,
        schema=NextBestAction,
        prompt_version=f"{rendered.name}.{rendered.version}",
        prompt_hash=rendered.hash,
        validator=validator,
        pii=inp.pii,
        language=inp.language,
    )
    version = f"{rendered.name}.{rendered.version}"
    if result.ok and result.parsed is not None:
        out = result.parsed
        return NbaDecision(
            out.action,
            out.rationale,
            out.confidence,
            list(out.cited_note_ids),
            source="llm",
            model=result.model,
            prompt_version=version,
            cost_usd=result.cost_usd,
        )
    if result.status in ("invalid", "max_tokens"):
        return NbaDecision(
            rules.action,
            "Model output failed validation twice; an analyst decides.",
            0.0,
            source="fallback",
            requires_analyst=True,
            analyst_reason="nba_invalid_output",
            model=result.model,
            prompt_version=version,
            cost_usd=result.cost_usd,
        )
    return NbaDecision(
        rules.action,
        rules.rationale,
        rules.confidence,
        source="fallback",
        model=result.model,
        prompt_version=version,
        cost_usd=result.cost_usd,
    )

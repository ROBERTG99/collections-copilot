"""Next-best-action node (FR-3): LLM only with notes, rules otherwise, validated output."""

from __future__ import annotations

from collections_copilot.agent.nba_rules import RuleFacts, decide
from collections_copilot.agent.nodes.nba import NbaInput, decide_nba
from collections_copilot.agent.prompts import UntrustedNote
from collections_copilot.config import Settings
from collections_copilot.llm.gateway import LLMGateway, routes
from collections_copilot.llm.providers.fake import FakeLLM, FakeMode

ROUTES = routes(Settings())


def facts(**kw: object) -> RuleFacts:
    base: dict[str, object] = {
        "open_balance_cents": 120_000,
        "max_days_past_due": 40,
        "broken_promises_90d": 0,
        "open_invoices": 1,
        "disputed_invoices": 0,
        "currency": "USD",
    }
    base.update(kw)
    return RuleFacts(**base)  # type: ignore[arg-type]


def nba_input(notes: list[UntrustedNote], **kw: object) -> NbaInput:
    return NbaInput(facts=facts(**kw), segment="smb", language="en", notes=notes)


async def test_dispute_note_yields_pause() -> None:
    fake = FakeLLM()
    notes = [
        UntrustedNote("11111111-1111-1111-1111-111111111111", "crm", "Left voicemail."),
        UntrustedNote(
            "22222222-2222-2222-2222-222222222222",
            "crm",
            "Customer disputes invoice INV-1: damaged goods.",
        ),
    ]
    decision = await decide_nba(LLMGateway(fake), ROUTES, nba_input(notes))
    assert decision.action == "pause"
    assert decision.cited_note_ids == ["22222222-2222-2222-2222-222222222222"]
    assert decision.source == "llm"
    assert not decision.requires_analyst
    assert len(fake.requests) == 1
    assert fake.requests[0].model == "claude-opus-5-5"


async def test_invalid_output_twice_routes_to_analyst() -> None:
    fake = FakeLLM(mode=FakeMode(invalid=frozenset({"next_best_action"})))
    notes = [UntrustedNote("n1", "crm", "Customer disputes invoice INV-1.")]
    decision = await decide_nba(LLMGateway(fake), ROUTES, nba_input(notes))
    assert decision.requires_analyst
    assert decision.analyst_reason == "nba_invalid_output"
    assert len(fake.requests) == 2


async def test_cited_ids_must_exist() -> None:
    class Liar(FakeLLM):
        async def complete(self, request):  # type: ignore[no-untyped-def]
            from collections_copilot.llm.providers.base import ProviderResponse

            self.requests.append(request)
            return ProviderResponse(
                '{"action": "pause", "rationale": "r", "confidence": 0.9, "cited_note_ids": ["made-up"]}',
                "end_turn",
                "fake",
            )

    liar = Liar()
    decision = await decide_nba(
        LLMGateway(liar), ROUTES, nba_input([UntrustedNote("n1", "crm", "hello")])
    )
    assert decision.requires_analyst
    assert decision.analyst_reason == "nba_invalid_output"


async def test_no_notes_uses_rules_without_llm() -> None:
    fake = FakeLLM()
    decision = await decide_nba(LLMGateway(fake), ROUTES, nba_input([]))
    assert decision.source == "rules"
    assert decision.action == "remind"
    assert fake.requests == []


async def test_provider_outage_falls_back_to_rules() -> None:
    fake = FakeLLM(mode=FakeMode(outage=True))
    gateway = LLMGateway(fake, sleep=_no_sleep)
    decision = await decide_nba(
        gateway, ROUTES, nba_input([UntrustedNote("n1", "crm", "please call")])
    )
    assert decision.source == "fallback"
    assert decision.fallback
    assert decision.action == "remind"


async def _no_sleep(_: float) -> None:
    return None


def test_rules() -> None:
    assert decide(facts()).action == "remind"
    assert decide(facts(open_invoices=0, open_balance_cents=0)).action == "no_action"
    assert (
        decide(facts(open_invoices=0, open_balance_cents=0, disputed_invoices=1)).action == "pause"
    )
    assert decide(facts(max_days_past_due=150, broken_promises_90d=2)).action == "escalate"
    assert (
        decide(facts(open_balance_cents=1_500_000, max_days_past_due=95)).action == "payment_plan"
    )

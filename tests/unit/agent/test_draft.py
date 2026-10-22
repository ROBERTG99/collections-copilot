"""Drafts (FR-4) and the priority rationale (FR-2)."""

from __future__ import annotations

from datetime import UTC, datetime

from collections_copilot.agent.nodes.draft import DraftInput, format_money, make_draft
from collections_copilot.agent.nodes.rationale import priority_rationale
from collections_copilot.compliance import engine
from collections_copilot.compliance.engine import AccountFacts, ComplianceContext, InvoiceFacts
from collections_copilot.compliance.rules.footer import footer_for
from collections_copilot.config import Settings
from collections_copilot.llm.gateway import LLMGateway, routes
from collections_copilot.llm.providers.fake import FakeLLM, FakeMode
from collections_copilot.llm.redaction import AccountPII
from collections_copilot.scoring.priority import PriorityInputs, score

ROUTES = routes(Settings())
INVOICES = [
    InvoiceFacts("F-2026-17", 120_050, "EUR", "open"),
    InvoiceFacts("F-2026-18", 30_000, "EUR", "open"),
    InvoiceFacts("F-2026-09", 99_900, "EUR", "disputed"),
]


def draft_input(**kw: object) -> DraftInput:
    base: dict[str, object] = {
        "action": "payment_plan",
        "language": "es",
        "account_name": "Granito Suministros S.L.",
        "currency": "EUR",
        "invoices": INVOICES,
        "pay_link": "https://pay.collections.example/p/abc123",
        "pii": AccountPII(name="Granito Suministros S.L.", phone_e164="+34555123456"),
    }
    base.update(kw)
    return DraftInput(**base)  # type: ignore[arg-type]


def postcheck(body: str, language: str = "es") -> engine.CheckResult:
    return engine.postcheck(
        ComplianceContext(
            now=datetime(2026, 3, 10, 11, 0, tzinfo=UTC),
            account=AccountFacts("Europe/Madrid", language, "EUR", opted_out=False),
            invoices=INVOICES,
            action="payment_plan",
            body=body,
        )
    )


async def test_spanish_draft_has_footer_and_ledger_amounts_only() -> None:
    fake = FakeLLM()
    result = await make_draft(LLMGateway(fake), ROUTES, draft_input())
    assert result.author == "ai"
    assert result.body.endswith(footer_for("es"))
    assert "plan de pagos" in result.body
    assert "Granito Suministros S.L." in result.body  # placeholder restored after the call
    assert "<ACCOUNT_NAME>" not in result.body
    assert "1.500,50 EUR" in result.body  # the open total, formatted from the ledger
    assert "999" not in result.body  # the disputed invoice is never offered to the model
    assert len(result.body) <= 320
    assert postcheck(result.body).ok, postcheck(result.body).violations
    sent = fake.requests[0]
    assert "Granito" not in sent.user  # redacted before the provider
    assert "F-2026-09" not in sent.user


async def test_english_reminder_and_template_fallback_on_refusal() -> None:
    usd = [InvoiceFacts("INV-1", 120_000, "USD", "open")]
    inp = draft_input(
        action="remind",
        language="en",
        currency="USD",
        invoices=usd,
        account_name="Cobalt Ridge LLC",
        pii=AccountPII(name="Cobalt Ridge LLC"),
    )
    fake = FakeLLM(mode=FakeMode(refusal=frozenset({"draft_message"})))
    result = await make_draft(LLMGateway(fake), ROUTES, inp)
    assert result.author == "fallback"
    assert result.body.startswith(
        "Hi Cobalt Ridge LLC, this is a reminder that invoice INV-1 for USD 1,200.00"
    )
    assert result.body.endswith(footer_for("en"))


async def test_feedback_from_failed_postcheck_is_sent() -> None:
    fake = FakeLLM()
    await make_draft(
        LLMGateway(fake), ROUTES, draft_input(feedback=["amount_not_in_ledger: 999,00"])
    )
    assert "amount_not_in_ledger" in fake.requests[0].user


def test_money_formatting() -> None:
    assert format_money(120_050, "EUR", "es") == "1.200,50 EUR"
    assert format_money(120_050, "USD", "en") == "USD 1,200.50"
    assert format_money(5, "USD", "en") == "USD 0.05"


async def test_rationale_never_changes_score_and_is_null_on_failure() -> None:
    s = score(PriorityInputs(250_000, "USD", 45, 1, "smb"))
    ok = await priority_rationale(LLMGateway(FakeLLM()), ROUTES, s)
    assert ok is not None
    assert str(s.score) in ok
    failing = LLMGateway(FakeLLM(mode=FakeMode(outage=True)), sleep=_no_sleep)
    assert await priority_rationale(failing, ROUTES, s) is None


async def _no_sleep(_: float) -> None:
    return None

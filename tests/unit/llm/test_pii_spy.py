"""T-18 / NFR-10: no seeded PII reaches a provider (spy on every provider payload)."""

from __future__ import annotations

import json

from collections_copilot.llm.redaction import AccountPII, Redactor
from data.generator.gen import standard_tenants


class SpyProvider:
    name = "spy"

    def __init__(self) -> None:
        self.payloads: list[str] = []

    async def complete(self, request):  # type: ignore[no-untyped-def]
        from collections_copilot.llm.providers.base import ProviderResponse

        self.payloads.append(request.system + "\n" + request.user)
        return ProviderResponse(text="{}", stop_reason="end_turn", model="spy")


def test_redacts_generic_pii_and_restores_placeholders() -> None:
    redactor = Redactor()
    pii = AccountPII(
        name="Cobalt Ridge Supply LLC", phone_e164="+15550001234", contacts=("Dana Example",)
    )
    text = (
        "Cobalt Ridge Supply LLC (Cobalt Ridge) called from +1 555 000 1234; Dana Example wrote from "
        "ap@cobalt.example. Card 4111 1111 1111 1111, IBAN ES9121000418450200051332. Invoice INV-A00001-1."
    )
    redacted, mapping = redactor.redact(text, pii)
    for secret in (
        "Cobalt Ridge",
        "555 000 1234",
        "Dana Example",
        "ap@cobalt.example",
        "4111",
        "ES9121",
    ):
        assert secret not in redacted, secret
    assert "INV-A00001-1" in redacted  # invoice numbers are needed for citations
    assert "<ACCOUNT_NAME>" in redacted
    assert redactor.restore("Hi <ACCOUNT_NAME>,", mapping) == "Hi Cobalt Ridge Supply LLC,"


async def test_provider_payload_has_no_seeded_pii() -> None:
    from collections_copilot.agent.prompts import render_for_spy

    redactor = Redactor()
    spy = SpyProvider()
    accounts = standard_tenants()[0].accounts[:300]
    for acc in accounts:
        await render_for_spy(spy, redactor, acc)
    assert len(spy.payloads) >= 300
    blob = "\n".join(spy.payloads)
    for acc in accounts:
        assert acc.name not in blob, acc.external_ref
        assert acc.phone_e164 not in blob, acc.external_ref
        assert acc.phone_e164[1:] not in blob, acc.external_ref
    assert json.dumps(blob)  # payloads are plain text


def test_long_invoice_numbers_and_dates_survive() -> None:
    redacted, _ = Redactor().redact("Invoice INV-2026-000123 due 2026-03-15, call (555) 010-2030")
    assert "INV-2026-000123" in redacted
    assert "2026-03-15" in redacted
    assert "010-2030" not in redacted


def test_spanish_local_numbers_are_redacted() -> None:
    """M2 review: Spanish numbers written without +34 never reach a provider."""
    for number in ("555 12 34 56", "555 123 456"):
        redacted, mapping = Redactor().redact(
            f"Llamar a Marta al {number} por la tarde", language="es"
        )
        assert number not in redacted
        assert number in mapping.values()


def test_amounts_and_invoice_numbers_survive_es_patterns() -> None:
    text = "Factura INV-2026-000123 de EUR 1.234,56 vence el 2026-03-15"
    redacted, _ = Redactor().redact(text, language="es")
    assert redacted == text

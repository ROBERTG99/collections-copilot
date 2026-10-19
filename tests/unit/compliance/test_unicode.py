"""M6 security audit H1: look-alike and hidden characters never get a link or phrase past the
post-check. The text rules read the normalised body and fail closed on anything they cannot."""

from __future__ import annotations

import pytest

from collections_copilot.compliance import engine
from tests.unit.compliance.test_rules import codes, ctx

FOOTER = " Reply STOP to opt out."
BASE = "Hi, invoice INV-1 for USD 1,200.00 is past due. Pay at "


@pytest.mark.parametrize(
    ("link", "code"),
    [
        ("раураl.com/acme", "non_latin_letters"),  # Cyrillic look-alikes of "paypal"
        ("evil​.com/pay", "invisible_characters"),  # zero-width space inside a host
        ("evil。com/pay", "link_not_allowed"),  # ideographic full stop
        ("evil．com/pay", "link_not_allowed"),  # fullwidth full stop
        ("evil[.]com/pay", "link_not_allowed"),
        ("10.0.0.1/pay", "link_not_allowed"),  # bare IP literal
        ("pay.collections.example‮/x", "invisible_characters"),  # bidi override
    ],
)
def test_hidden_or_look_alike_links_are_blocked(link: str, code: str) -> None:
    result = engine.postcheck(ctx(body=BASE + link + FOOTER))
    assert not result.ok
    assert code in codes(result)


@pytest.mark.parametrize(
    "phrase",
    [
        "We will ѕue you.",  # Cyrillic dze for "s"
        "Next step is j​ail.",
        "This is your ＦＩＮＡＬ ＷＡＲＮＩＮＧ.",  # fullwidth letters
    ],
)
def test_disguised_threats_are_blocked(phrase: str) -> None:
    body = "Hi, invoice INV-1 for USD 1,200.00 is past due. " + phrase + FOOTER
    assert not engine.postcheck(ctx(body=body)).ok


def test_fullwidth_amounts_are_checked_against_the_ledger() -> None:
    body = "Hi, invoice INV-1 for USD ９９９.００ is past due." + FOOTER
    assert "amount_not_in_ledger" in codes(engine.postcheck(ctx(body=body)))


def test_spanish_accents_and_the_account_name_are_fine() -> None:
    es = ctx(
        account=engine.AccountFacts(
            timezone="Europe/Madrid", language="es", currency="EUR", opted_out=False
        ),
        invoices=[engine.InvoiceFacts("INV-1", 120_000, "EUR", "open")],
        body="Hola, la factura INV-1 por 1.200,00 € está vencida. Pague en "
        "https://pay.collections.example/x Responda BAJA para no recibir más mensajes.",
    )
    result = engine.postcheck(es)
    assert "non_latin_letters" not in codes(result)
    assert "invisible_characters" not in codes(result)

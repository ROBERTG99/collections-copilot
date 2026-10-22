"""Debtor simulator personas and replies (demo only, synthetic numbers)."""

from __future__ import annotations

import pytest
from debtor_sim.main import persona_for, reply_text

REMINDER_EN = (
    "Hi Zephyr Builders Ltd., a reminder that invoice INV-A00038-1 (USD 1,959.00) is past due. "
    "Open balance: USD 1,959.00. You can pay at https://pay.collections.example/p/x. "
    "Reply STOP to opt out."
)
REMINDER_ES = (
    "Hola Zephyr S.L., le recordamos que la factura F-1 por EUR 1.234,56 está vencida. "
    "Saldo pendiente: EUR 1.234,56. Responde STOP para darte de baja."
)


@pytest.mark.parametrize(
    ("phone", "persona"),
    [
        ("+15550000000", "promise"),
        ("+15550000004", "dispute"),
        ("+15550000006", "paid"),
        ("+15550000007", "stop"),
        ("+15550000009", "silent"),
    ],
)
def test_persona_by_last_digit(phone: str, persona: str) -> None:
    assert persona_for(phone) == persona
    assert persona_for(phone, "paid") == "paid"


def test_promise_quotes_the_open_balance() -> None:
    assert reply_text("promise", REMINDER_EN) == "I can pay 1,959.00 on Friday"
    assert reply_text("promise", REMINDER_ES) == "Puedo pagar 1.234,56 el viernes"


def test_no_reply_to_silent_personas_or_non_reminders() -> None:
    assert reply_text("silent", REMINDER_EN) is None
    assert reply_text("promise", "You are unsubscribed and will get no more messages.") is None
    assert reply_text("stop", REMINDER_EN) == "STOP"


def test_payment_plan_offer_gets_a_promise_of_the_balance() -> None:
    offer = (
        "Hi Alder Labs Group, we can set up a payment plan for your balance of USD 85,688.48. "
        "Reply to this message to arrange it. Reply STOP to opt out."
    )
    assert reply_text("promise", offer) == "I can pay 85,688.48 on Friday"


def test_amounts_written_before_the_currency_are_read() -> None:
    """Spanish payment-plan drafts write `10.733,50 EUR`; the debtor still answers."""
    from debtor_sim.main import reply_text

    body = (
        "Hola Pioneer Logistics Partners, podemos acordar un plan de pagos para su saldo de "
        "10.733,50 EUR. Responda a este mensaje para coordinarlo. Responda BAJA para no recibir más."
    )
    text = reply_text("promise", body)
    assert text is not None
    assert "10.733,50" in text

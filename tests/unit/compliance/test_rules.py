"""T-14: each deterministic compliance rule (FR-5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from collections_copilot.compliance import engine
from collections_copilot.compliance.engine import AccountFacts, ComplianceContext, InvoiceFacts
from collections_copilot.compliance.rules import amounts, footer, links, phrases, window

NY = "America/New_York"


def ctx(**kw: object) -> ComplianceContext:
    base: dict[str, object] = {
        "now": datetime(2026, 3, 10, 15, 0, tzinfo=UTC),  # 11:00 in New York
        "account": AccountFacts(timezone=NY, language="en", currency="USD", opted_out=False),
        "invoices": [
            InvoiceFacts("INV-1", 120_000, "USD", "open"),
            InvoiceFacts("INV-2", 45_050, "USD", "open"),
        ],
        "contacts_7d": [],
        "action": "remind",
        "body": "Hi, invoice INV-1 for USD 1,200.00 is past due. Pay at https://pay.collections.example/x "
        "Reply STOP to opt out.",
    }
    base.update(kw)
    return ComplianceContext(**base)  # type: ignore[arg-type]


def codes(result: engine.CheckResult) -> set[str]:
    return {v.code for v in result.violations}


def test_clean_message_passes() -> None:
    result = engine.postcheck(ctx())
    assert result.ok, result.violations
    assert not result.deferred


def test_approval_at_2055_local_defers_to_0800() -> None:
    at_2055 = datetime(2026, 3, 10, 20, 55, tzinfo=ZoneInfo(NY)).astimezone(UTC)
    result = engine.postcheck(ctx(now=at_2055))
    assert result.ok
    assert result.deferred
    assert result.send_at.astimezone(ZoneInfo(NY)) == datetime(
        2026, 3, 11, 8, 0, tzinfo=ZoneInfo(NY)
    )


def test_before_window_defers_same_day() -> None:
    at_0630 = datetime(2026, 3, 10, 6, 30, tzinfo=ZoneInfo(NY)).astimezone(UTC)
    result = engine.postcheck(ctx(now=at_0630))
    assert result.send_at.astimezone(ZoneInfo(NY)).hour == 8
    assert result.send_at.astimezone(ZoneInfo(NY)).day == 10


def test_dst_spring_forward_window_start() -> None:
    # 2026-03-08 is the US DST change; 08:00 local exists and is 12:00 UTC.
    night = datetime(2026, 3, 8, 3, 30, tzinfo=UTC)  # 22:30 on 2026-03-07 New York (EST)
    start = window.next_send_time(night, NY)
    assert start == datetime(2026, 3, 8, 12, 0, tzinfo=UTC)


def test_madrid_and_canary_windows() -> None:
    utc_0705 = datetime(2026, 6, 1, 7, 5, tzinfo=UTC)
    assert window.in_window(utc_0705, "Atlantic/Canary")  # 08:05 WEST
    assert window.in_window(utc_0705, "Europe/Madrid")  # 09:05 CEST
    utc_0605 = datetime(2026, 6, 1, 6, 5, tzinfo=UTC)
    assert not window.in_window(utc_0605, "Atlantic/Canary")  # 07:05


def test_amount_not_in_ledger_blocks() -> None:
    body = "You owe USD 999.00 today. Reply STOP to opt out."
    result = engine.postcheck(ctx(body=body))
    assert "amount_not_in_ledger" in codes(result)
    assert not result.ok


@pytest.mark.parametrize(
    ("text", "currency"),
    [
        ("Balance $1,650.50 due.", "USD"),
        ("Saldo 1.650,50 € pendiente.", "EUR"),
        ("Saldo EUR 450,50 pendiente.", "EUR"),
        ("Balance of 1200.00 USD.", "USD"),
        ("Invoice INV-2: US$450.50.", "USD"),
        ("Balance 1,650.50 due.", "USD"),
    ],
)
def test_ledger_amounts_accepted(text: str, currency: str) -> None:
    invoices = [InvoiceFacts(i.number, i.amount_cents, currency, i.status) for i in ctx().invoices]
    allowed = amounts.allowed_amounts(invoices)
    assert amounts.find_unknown(text, allowed, invoices) == [], text


def test_amount_extraction_flags_bare_decimal_and_ignores_invoice_numbers() -> None:
    allowed = amounts.allowed_amounts(ctx().invoices)
    assert amounts.find_unknown("Pay 999.00 now, ref INV-2026-77.", allowed) == ["999.00"]
    assert amounts.find_unknown("Call us on day 15 about INV-1.", allowed) == []


def test_missing_footer_blocks_and_spanish_footer_accepted() -> None:
    result = engine.postcheck(ctx(body="Invoice INV-1 is past due."))
    assert "missing_footer" in codes(result)
    es = ctx(
        account=AccountFacts(
            timezone="Europe/Madrid", language="es", currency="EUR", opted_out=False
        ),
        invoices=[InvoiceFacts("F-1", 45_050, "EUR", "open")],
        now=datetime(2026, 3, 10, 10, 0, tzinfo=UTC),
        body="Hola, la factura F-1 de 450,50 € está vencida. Responde STOP para darte de baja.",
    )
    assert engine.postcheck(es).ok
    assert footer.footer_for("es") in footer.FOOTERS.values()


def test_forbidden_phrases_block() -> None:
    for bad in ("We will sue you", "you could be arrested", "Iremos a la policía", "FINAL WARNING"):
        assert phrases.find(bad), bad
    result = engine.postcheck(ctx(body="We will sue you. Reply STOP to opt out."))
    assert "forbidden_phrase" in codes(result)


def test_links_must_be_allowlisted() -> None:
    assert links.find_disallowed("pay at https://pay.collections.example/abc") == []
    assert links.find_disallowed("see http://pay-now.example.net/x") == ["pay-now.example.net"]
    assert links.find_disallowed("go to www.evil.example.org") == ["www.evil.example.org"]
    result = engine.postcheck(
        ctx(body="Pay at http://evil.example.net now. Reply STOP to opt out.")
    )
    assert "link_not_allowed" in codes(result)


def test_opt_out_blocks_everything() -> None:
    account = AccountFacts(timezone=NY, language="en", currency="USD", opted_out=True)
    assert "opted_out" in codes(engine.precheck(ctx(account=account)))
    assert "opted_out" in codes(engine.postcheck(ctx(account=account)))


def test_contact_cap() -> None:
    now = ctx().now
    three = [now - timedelta(days=d) for d in (1, 2, 6)]
    assert "contact_cap_reached" in codes(engine.precheck(ctx(contacts_7d=three)))
    old = [now - timedelta(days=d) for d in (1, 2, 8)]
    assert engine.precheck(ctx(contacts_7d=old)).ok


def test_reminder_on_disputed_invoice_blocked() -> None:
    invoices = [
        InvoiceFacts("INV-1", 120_000, "USD", "disputed"),
        InvoiceFacts("INV-2", 45_050, "USD", "open"),
    ]
    body = "Invoice INV-1 for USD 1,200.00 is past due. Reply STOP to opt out."
    result = engine.postcheck(ctx(invoices=invoices, body=body))
    assert "disputed_invoice" in codes(result)
    ok_body = "Invoice INV-2 for USD 450.50 is past due. Reply STOP to opt out."
    assert engine.postcheck(ctx(invoices=invoices, body=ok_body)).ok
    all_disputed = [InvoiceFacts("INV-1", 120_000, "USD", "disputed")]
    assert "all_invoices_disputed" in codes(engine.precheck(ctx(invoices=all_disputed)))


def test_too_long_blocks() -> None:
    body = "x" * 300 + " Reply STOP to opt out."
    assert "too_long" in codes(engine.postcheck(ctx(body=body)))


def test_empty_body_blocks() -> None:
    assert "empty_body" in codes(engine.postcheck(ctx(body="  ")))


def test_violation_payload_is_serialisable() -> None:
    result = engine.postcheck(ctx(body="We will sue. USD 5.00"))
    data = result.as_dict()
    assert data["ok"] is False
    assert {"code", "message", "blocking"} <= set(data["violations"][0])


@pytest.mark.parametrize(
    ("token", "cents"),
    [
        ("1.234", 123_400),
        ("1,234", 123_400),
        ("12,5", 1_250),
        ("1.234,56", 123_456),
        ("1,234.56", 123_456),
        ("999", 99_900),
        ("1 234,50", 123_450),
    ],
)
def test_to_cents(token: str, cents: int) -> None:
    assert amounts.to_cents(token) == cents


def test_to_cents_rejects_garbage() -> None:
    assert amounts.to_cents("x,yz") is None


# --- M1 review regressions: the amount and link checks must fail closed ---------------------------


@pytest.mark.parametrize(
    "text",
    [
        "You owe 999 dollars.",
        "Saldo: 999 pesos.",
        "Pay USD999.00 now.",
        "Pay 999.00USD now.",
        "Pay $1k today.",
        "pay 1,234 today",
        "Balance GBP 450.50",
        "Balance £450.50",
        "Total 1200.00 MXN",
    ],
)
def test_unknown_amounts_fail_closed(text: str) -> None:
    allowed = amounts.allowed_amounts([InvoiceFacts("INV-1", 10_000, "USD", "open")])
    assert amounts.find_unknown(text, allowed) != [], text


@pytest.mark.parametrize(
    "text",
    [
        "Invoice INV-1 for USD 100.00 is due on 2026-03-15.",
        "Reply within 3 days. Ref W-4821.",
        "Call us at 09:30 about INV-1.",
        "Pay at https://pay.collections.example/a/123456 today.",
    ],
)
def test_identifiers_dates_and_small_counts_are_not_amounts(text: str) -> None:
    invoices = [InvoiceFacts("INV-1", 10_000, "USD", "open")]
    assert amounts.find_unknown(text, amounts.allowed_amounts(invoices), invoices) == [], text


def test_currency_must_match_the_invoice() -> None:
    invoices = [InvoiceFacts("INV-1", 120_000, "USD", "open")]
    allowed = amounts.allowed_amounts(invoices)
    assert amounts.find_unknown("EUR 1,200.00", allowed) == ["1,200.00"]
    assert amounts.find_unknown("USD 1,200.00", allowed) == []


@pytest.mark.parametrize(
    "text",
    ["see goo.gl/abc", "rb.gy/x", "evil.app/pay", "pago.mx", "pay.collections.example.evil.ru/x"],
)
def test_any_unlisted_host_is_flagged(text: str) -> None:
    assert links.find_disallowed(text) != [], text


@pytest.mark.parametrize("text", ["Acme S.L. owes", "e.g. invoice", "version 1.2.3", "INV-1."])
def test_link_check_ignores_non_hosts(text: str) -> None:
    assert links.find_disallowed(text) == [], text


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        window.in_window(datetime(2026, 1, 1, 12, 0), "UTC")


def test_ordinals_ignored_and_extra_amounts_allowed() -> None:
    invoices = [InvoiceFacts("INV-1", 10_000, "USD", "open")]
    assert (
        amounts.find_unknown("Due on the 115th day", amounts.allowed_amounts(invoices), invoices)
        == []
    )
    allowed = amounts.allowed_amounts(invoices, extra_cents=[120_000])
    assert amounts.find_unknown("Thanks, we recorded USD 1,200.00.", allowed, invoices) == []


@pytest.mark.parametrize(
    ("number", "body"),
    [
        ("999", "You owe $999 now"),
        ("100234", "Pay 100234 dollars"),
        ("9", "Pay $999.00"),
        ("INV-1", "Balance -999.00"),
    ],
)
def test_numeric_invoice_numbers_cannot_mask_amounts(number: str, body: str) -> None:
    invoices = [InvoiceFacts(number, 10_000, "USD", "open")]
    assert amounts.find_unknown(body, amounts.allowed_amounts(invoices), invoices) != [], body


def test_numeric_invoice_number_as_reference_is_not_an_amount() -> None:
    invoices = [InvoiceFacts("100234", 10_000, "USD", "open")]
    body = "Invoice 100234 for USD 100.00 is past due."
    assert amounts.find_unknown(body, amounts.allowed_amounts(invoices), invoices) == []


def test_account_name_digits_are_not_amounts() -> None:
    body = "Hi Studio 1234 LLC, invoice INV-1 for USD 1,200.00 is past due. Reply STOP to opt out."
    result = engine.postcheck(ctx(body=body, account_name="Studio 1234 LLC"))
    assert result.ok, result.violations
    assert "amount_not_in_ledger" in codes(engine.postcheck(ctx(body=body)))

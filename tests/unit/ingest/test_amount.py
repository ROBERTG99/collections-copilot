"""T-51 security audit M3: an amount is plain decimal notation with a bound, so one row can
neither stall the event loop (`1e999990` took 24 s of CPU) nor overflow the bigint column."""

from __future__ import annotations

import time
from decimal import Decimal

import pytest
from pydantic import ValidationError

from collections_copilot.ingest.schemas import AgingRow

ROW = {
    "external_ref": "A-1",
    "name": "Synthetic Co",
    "phone_e164": "+15550001234",
    "timezone": "America/New_York",
    "segment": "smb",
    "language": "en",
    "currency": "USD",
    "invoice_number": "INV-1",
    "issued_on": "2026-08-01",
    "due_on": "2026-09-01",
}


@pytest.mark.parametrize("amount", ["1200.50", "0.01", "99999999999.99", "7"])
def test_plain_amounts_are_accepted(amount: str) -> None:
    row = AgingRow(**ROW, amount=amount)
    assert row.amount_cents == int(Decimal(amount) * 100)


@pytest.mark.parametrize(
    "amount", ["1e3", "1E999990", "100000000000.00", "1e20", "-5", "0", "1.234", "NaN", " 1_000"]
)
def test_exponents_huge_and_malformed_amounts_are_rejected(amount: str) -> None:
    started = time.process_time()
    with pytest.raises(ValidationError, match="amount"):
        AgingRow(**ROW, amount=amount)
    assert time.process_time() - started < 0.5

"""Properties: the engine never allows a send outside the window, over the cap or after opt-out."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from hypothesis import given, settings
from hypothesis import strategies as st

from collections_copilot.compliance import engine
from collections_copilot.compliance.engine import AccountFacts, ComplianceContext, InvoiceFacts
from collections_copilot.compliance.rules import window

ZONES = [
    "America/New_York",
    "America/Los_Angeles",
    "America/Phoenix",
    "Europe/Madrid",
    "Atlantic/Canary",
    "Pacific/Auckland",
    "Asia/Kolkata",
]
instants = st.datetimes(
    min_value=datetime(2025, 1, 1), max_value=datetime(2028, 12, 31), timezones=st.just(UTC)
)
BODY = "Invoice INV-1 for USD 1,200.00 is past due. Reply STOP to opt out."


def _ctx(
    now: datetime, tz: str, contacts: list[datetime] | None = None, opted_out: bool = False
) -> ComplianceContext:
    return ComplianceContext(
        now=now,
        account=AccountFacts(timezone=tz, language="en", currency="USD", opted_out=opted_out),
        invoices=[InvoiceFacts("INV-1", 120_000, "USD", "open")],
        contacts_7d=contacts or [],
        action="remind",
        body=BODY,
    )


@settings(max_examples=400, deadline=None)
@given(now=instants, tz=st.sampled_from(ZONES))
def test_never_allows_outside_window(now: datetime, tz: str) -> None:
    result = engine.postcheck(_ctx(now, tz))
    local = result.send_at.astimezone(ZoneInfo(tz))
    assert result.send_at >= now
    assert window.WINDOW_START <= local.time() < window.WINDOW_END
    assert result.send_at - now <= timedelta(hours=26)
    # the planned send leaves the buffer before the window closes
    end = local.replace(
        hour=window.WINDOW_END.hour, minute=window.WINDOW_END.minute, second=0, microsecond=0
    )
    assert end - local >= window.END_BUFFER


@settings(max_examples=200, deadline=None)
@given(
    now=instants,
    offsets=st.lists(st.floats(min_value=0, max_value=6.99), min_size=3, max_size=6),
)
def test_never_allows_over_cap(now: datetime, offsets: list[float]) -> None:
    contacts = [now - timedelta(days=o) for o in offsets]
    assert not engine.precheck(_ctx(now, "America/New_York", contacts)).ok
    assert not engine.postcheck(_ctx(now, "America/New_York", contacts)).ok


@settings(max_examples=100, deadline=None)
@given(now=instants, tz=st.sampled_from(ZONES))
def test_never_allows_after_opt_out(now: datetime, tz: str) -> None:
    assert not engine.precheck(_ctx(now, tz, opted_out=True)).ok
    assert not engine.postcheck(_ctx(now, tz, opted_out=True)).ok

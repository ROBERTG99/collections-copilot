"""Contact window: 08:00–21:00 debtor local time, with a 10-minute buffer before the close.

An approval at 20:55 local is therefore deferred to 08:00 the next morning (FR-5). Local times come
from IANA zones via zoneinfo, so DST transitions are handled by construction.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

WINDOW_START = time(8, 0)
WINDOW_END = time(21, 0)
END_BUFFER = timedelta(minutes=10)
LAST_SEND = time(20, 50)  # WINDOW_END - END_BUFFER


def _require_aware(now: datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")


def in_window(now: datetime, tz: str) -> bool:
    _require_aware(now)
    local = now.astimezone(ZoneInfo(tz)).time()
    return WINDOW_START <= local < LAST_SEND


def next_send_time(now: datetime, tz: str) -> datetime:
    """`now` if inside the window, else the next 08:00 local (returned in the input's zone)."""
    _require_aware(now)
    zone = ZoneInfo(tz)
    local = now.astimezone(zone)
    if in_window(now, tz):
        return now
    day = local.date() if local.time() < WINDOW_START else local.date() + timedelta(days=1)
    start = datetime(
        day.year, day.month, day.day, WINDOW_START.hour, WINDOW_START.minute, tzinfo=zone
    )
    return start.astimezone(now.tzinfo)

"""Shared test helpers: polling without sleeps-as-assertions."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable


async def eventually[T](
    probe: Callable[[], Awaitable[T | None]], timeout_s: float = 10.0, interval_s: float = 0.05
) -> T:
    """Poll `probe` until it returns a truthy value or the timeout expires."""
    deadline = time.monotonic() + timeout_s
    last: T | None = None
    while time.monotonic() < deadline:
        last = await probe()
        if last:
            return last
        await asyncio.sleep(interval_s)
    raise AssertionError(f"condition not met within {timeout_s}s (last={last!r})")

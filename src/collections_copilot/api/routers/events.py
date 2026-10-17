"""Live case progress over SSE (FR-11): `node`, `draft`, `status` and `heartbeat` events.

Events come from the case's Redis stream. Each SSE `id:` is the stream id, so a browser that
reconnects with `Last-Event-ID` receives only later events; without it, the retained history
(last 200) is replayed first. A heartbeat goes out after `CC_SSE_HEARTBEAT_S` (15 s) of silence.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from redis.exceptions import RedisError

from collections_copilot.api.deps.auth import ANY_ROLE, Principal
from collections_copilot.api.errors import ProblemError
from collections_copilot.db.repositories import Repos
from collections_copilot.observability.events import EVENT_ID, RedisEventReader

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["events"])


def _frame(event: str, data: str, event_id: str | None = None) -> str:
    head = f"id: {event_id}\n" if event_id else ""
    return f"{head}event: {event}\ndata: {data}\n\n"


class StreamSlots:
    """Open progress streams per user, in this API process."""

    def __init__(self, per_user: int) -> None:
        self.per_user = per_user
        self.open: dict[str, int] = {}

    def acquire(self, holder: str) -> bool:
        if self.open.get(holder, 0) >= self.per_user:
            return False
        self.open[holder] = self.open.get(holder, 0) + 1
        return True

    def release(self, holder: str) -> None:
        left = self.open.get(holder, 0) - 1
        if left > 0:
            self.open[holder] = left
        else:
            self.open.pop(holder, None)


@router.get(
    "/cases/{case_id}/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}, 404: {}},
)
async def case_events(
    case_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(ANY_ROLE),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID", max_length=40),
) -> StreamingResponse:
    async with request.app.state.sessionmaker() as s:
        case = await Repos(s).cases.get(principal.tenant_id, case_id)
    if case is None:
        raise ProblemError(404, "case not found", code="not_found")
    after = last_event_id if last_event_id and EVENT_ID.match(last_event_id) else "0"
    slots: StreamSlots = request.app.state.stream_slots
    holder = f"{principal.tenant_id}:{principal.username}"
    if not slots.acquire(holder):
        raise ProblemError(
            429,
            f"at most {slots.per_user} open progress streams per user",
            code="too_many_streams",
            headers={"Retry-After": "5"},
        )
    # Streams block on XREAD: they use their own connection pool, never the limiter's.
    reader = RedisEventReader(request.app.state.redis_streams)
    heartbeat_ms = int(request.app.state.settings.sse_heartbeat_s * 1000)
    tenant, case_key = str(principal.tenant_id), str(case_id)

    async def stream() -> AsyncIterator[str]:
        try:
            async for frame in frames():
                yield frame
        finally:
            slots.release(holder)

    async def frames() -> AsyncIterator[str]:
        nonlocal after
        yield "retry: 3000\n\n"
        while not await request.is_disconnected():
            if principal.expires_at and time.time() >= principal.expires_at:
                # The token expired: end the stream; the BFF refreshes on reconnect.
                yield _frame("error", '{"detail": "session expired; reconnect"}')
                return
            try:
                events = await reader.read(tenant, case_key, after, heartbeat_ms)
            except RedisError:
                log.warning("event stream unavailable")
                yield _frame("error", '{"detail": "progress temporarily unavailable"}')
                return
            if not events:
                yield _frame("heartbeat", "{}")
                continue
            for e in events:
                after = e.id
                yield _frame(e.event, e.data, e.id)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

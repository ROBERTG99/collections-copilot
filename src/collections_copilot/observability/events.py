"""Case progress events for SSE (FR-11). Redis publisher in production; in-memory in tests."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

log = logging.getLogger(__name__)


class EventPublisher(Protocol):
    async def publish(
        self, tenant_id: str, case_id: str, event: str, data: dict[str, Any]
    ) -> None: ...


class NullEventPublisher:
    async def publish(self, tenant_id: str, case_id: str, event: str, data: dict[str, Any]) -> None:
        return None


@dataclass
class InMemoryEventPublisher:
    events: list[tuple[str, str, str, dict[str, Any]]] = field(default_factory=list)

    async def publish(self, tenant_id: str, case_id: str, event: str, data: dict[str, Any]) -> None:
        self.events.append((tenant_id, case_id, event, data))


STREAM_MAXLEN = 200
STREAM_TTL_S = 7 * 24 * 3600
EVENT_ID = re.compile(r"^\d{1,20}-\d{1,10}$")


def stream_key(tenant_id: str, case_id: str) -> str:
    return f"cc:events:{tenant_id}:{case_id}"


class RedisEventPublisher:
    """Appends each event to the case's Redis stream (last 200 kept, 7-day TTL).

    The stream serves both live delivery (blocking XREAD) and `Last-Event-ID` resume, so a
    reconnecting browser gets exactly the events after the one it saw. Publishing is best effort:
    progress events never fail a graph node."""

    def __init__(self, client: Redis) -> None:
        self.redis = client

    async def publish(self, tenant_id: str, case_id: str, event: str, data: dict[str, Any]) -> None:
        key = stream_key(tenant_id, case_id)
        try:
            pipe = self.redis.pipeline(transaction=False)
            pipe.xadd(
                key,
                {"event": event, "data": json.dumps(data, default=str)},
                maxlen=STREAM_MAXLEN,
                approximate=True,
            )
            pipe.expire(key, STREAM_TTL_S)
            await pipe.execute()
        except RedisError:
            log.warning("progress event not published", extra={"event": event})


@dataclass(frozen=True)
class StoredEvent:
    id: str
    event: str
    data: str


class RedisEventReader:
    def __init__(self, client: Redis) -> None:
        self.redis = client

    async def read(
        self, tenant_id: str, case_id: str, after: str, block_ms: int
    ) -> list[StoredEvent]:
        """Events after `after` ("0" for all retained); waits up to `block_ms` for new ones."""
        result: Any = await self.redis.xread(
            {stream_key(tenant_id, case_id): after}, count=100, block=block_ms
        )
        out = []
        for _, entries in result or []:
            for entry_id, fields in entries:
                out.append(
                    StoredEvent(
                        _text(entry_id),
                        _text(fields.get(b"event", b"")),
                        _text(fields.get(b"data", b"{}")),
                    )
                )
        return out


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)

"""Case progress events for SSE (FR-11). Redis publisher in production; in-memory in tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


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

"""Dependencies the case and reply graphs need, bundled so tests and evals can swap them."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collections_copilot.agent.store import CaseStore
from collections_copilot.config import Settings
from collections_copilot.db.repositories import Repos
from collections_copilot.integrations.email.smtp import InMemoryNotifier, Notifier
from collections_copilot.integrations.sms.base import InMemorySmsSender, SmsSender
from collections_copilot.llm.gateway import LLMGateway, RouteSpec, routes
from collections_copilot.observability.events import EventPublisher, NullEventPublisher
from collections_copilot.observability.steps import StepRecord


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Services:
    settings: Settings
    store: CaseStore
    gateway: LLMGateway
    sms: SmsSender = field(default_factory=InMemorySmsSender)
    notifier: Notifier = field(default_factory=InMemoryNotifier)
    events: EventPublisher = field(default_factory=NullEventPublisher)
    clock: Callable[[], datetime] = _now
    pay_link_base: str = "https://pay.collections.example/p"
    ledger: object | None = None  # LedgerTools for the reply graph
    enqueue: object | None = None  # async callable(task) for follow-up work

    @property
    def routes(self) -> dict[str, RouteSpec]:
        return routes(self.settings)


class DbStepSink:
    """Persists step records (one per node and per model call) to `step_records`."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self.sm = sessionmaker

    async def record(self, record: StepRecord) -> None:
        if not record.tenant_id:
            return
        import uuid

        async with self.sm() as s, s.begin():
            await Repos(s).steps.add(
                uuid.UUID(record.tenant_id),
                case_id=uuid.UUID(record.case_id) if record.case_id else None,
                run_id=record.run_id or "-",
                task_id=record.task_id or "-",
                step=record.step,
                node=record.node,
                kind=record.kind,
                model=record.model,
                prompt_version=record.prompt_version,
                prompt_hash=record.prompt_hash,
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                cost_usd=record.cost_usd,
                latency_ms=record.latency_ms,
                status=record.status,
                error=record.error,
                started_at=record.started_at,
            )

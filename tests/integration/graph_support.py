"""Helpers to run the case graph against the integration database."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collections_copilot.agent.case_graph import build_case_graph
from collections_copilot.agent.checkpointer import open_checkpointer
from collections_copilot.agent.services import Services
from collections_copilot.agent.store import DbCaseStore
from collections_copilot.config import Settings
from collections_copilot.db.repositories import Repos
from collections_copilot.integrations.sms.base import InMemorySmsSender
from collections_copilot.llm.gateway import LLMGateway
from collections_copilot.llm.providers.fake import FakeLLM
from collections_copilot.observability.events import InMemoryEventPublisher

# 15:00 UTC = 10:00/11:00 in New York: inside the contact window.
FIXED_NOW = datetime.now(UTC).replace(hour=15, minute=0, second=0, microsecond=0)


def make_services(
    sm: async_sessionmaker[AsyncSession],
    database_url: str,
    llm: FakeLLM | None = None,
    sms: InMemorySmsSender | None = None,
    **settings: Any,
) -> Services:
    cfg = Settings(database_url=database_url, **settings)
    return Services(
        settings=cfg,
        store=DbCaseStore(sm),
        gateway=LLMGateway(llm or FakeLLM(), sleep=_no_sleep),
        sms=sms or InMemorySmsSender(),
        events=InMemoryEventPublisher(),
        clock=lambda: FIXED_NOW,
    )


async def _no_sleep(_: float) -> None:
    return None


@asynccontextmanager
async def case_graph(services: Services, database_url: str) -> AsyncIterator[Any]:
    async with open_checkpointer(database_url, max_size=4) as saver:
        yield build_case_graph(services, saver)


async def new_case(
    sm: async_sessionmaker[AsyncSession], tenant: uuid.UUID, account: uuid.UUID
) -> str:
    async with sm() as s, s.begin():
        case = await Repos(s).cases.create(tenant, account, status="queued")
        return str(case.id)


def config(case_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": case_id}}


async def approve_latest(
    sm: async_sessionmaker[AsyncSession],
    tenant: uuid.UUID,
    case_id: str,
    actor: str = "ana.analyst",
) -> int:
    """What the approval API does before it enqueues resume_case."""
    async with sm() as s, s.begin():
        repos = Repos(s)
        draft = await repos.drafts.latest(tenant, uuid.UUID(case_id))
        assert draft is not None
        await repos.approvals.add(
            tenant,
            draft_id=draft.id,
            case_id=uuid.UUID(case_id),
            decision="approve",
            decided_by=actor,
            policy_snapshot={},
            send_at=FIXED_NOW,
        )
        return draft.version

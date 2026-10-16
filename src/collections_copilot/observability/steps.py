"""Shared step records (one per graph node, model call or tool call) and their sinks."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, Field

StepKind = Literal["llm", "tool", "node", "rule"]
StepStatus = Literal["ok", "error", "fallback", "refusal", "invalid", "max_tokens", "circuit_open"]


class StepRecord(BaseModel):
    run_id: str = ""
    task_id: str = ""
    tenant_id: str = ""
    case_id: str | None = None
    step: int = 0
    node: str
    kind: StepKind = "node"
    model: str | None = None
    prompt_version: str | None = None
    prompt_hash: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    status: StepStatus = "ok"
    error: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class StepContext:
    run_id: str = ""
    task_id: str = ""
    tenant_id: str = ""
    case_id: str | None = None
    node: str = ""


_current: ContextVar[StepContext | None] = ContextVar("cc_step_context", default=None)


def current_context() -> StepContext:
    return _current.get() or StepContext()


@contextlib.contextmanager
def step_context(ctx: StepContext) -> Iterator[StepContext]:
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


@contextlib.contextmanager
def node_context(node: str) -> Iterator[StepContext]:
    with step_context(replace(current_context(), node=node)) as ctx:
        yield ctx


class StepSink(Protocol):
    async def record(self, record: StepRecord) -> None: ...


class InMemoryStepSink:
    def __init__(self) -> None:
        self.records: list[StepRecord] = []

    async def record(self, record: StepRecord) -> None:
        record.step = len(self.records)
        self.records.append(record)


class NullStepSink:
    async def record(self, record: StepRecord) -> None:
        return None

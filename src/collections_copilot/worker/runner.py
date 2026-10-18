"""Applies cc.task.v1 messages to the case graph idempotently.

SQS delivers at least once, so every handler first reads the checkpoint:
- process_case: start the graph if no checkpoint exists, continue from the last checkpoint after a
  crash, otherwise ignore the duplicate.
- resume_case: resume only when the graph is paused at an interrupt of the expected kind
  (approval, deferred_send, await_reply); anything else is a stale or duplicate delivery.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from langgraph.types import Command

from collections_copilot.integrations.queue.messages import TaskMessage
from collections_copilot.observability.steps import StepContext, step_context

log = logging.getLogger(__name__)


Lock = Callable[[str], AbstractAsyncContextManager[None]]
WriteBack = Callable[[str, str], Awaitable[str]]


@contextlib.asynccontextmanager
async def _no_lock(_: str) -> AsyncIterator[None]:
    yield


class CaseRunner:
    """`lock(case_id)` serialises deliveries for one case across workers (duplicate SQS
    deliveries must not run the same thread twice); `write_back(tenant_id, promise_id)` runs
    the ledger retry after a supervisor approved a held promise."""

    def __init__(
        self, graph: Any, lock: Lock | None = None, write_back: WriteBack | None = None
    ) -> None:
        self.graph = graph
        self.lock = lock or _no_lock
        self.write_back = write_back

    async def handle(self, task: TaskMessage) -> str:
        if task.kind == "retry_write_back":
            promise_id = (task.resume or {}).get("promise_id")
            if self.write_back is None or not promise_id:
                raise ValueError("retry_write_back needs a promise_id and a write-back handler")
            async with self.lock(f"promise:{promise_id}"):
                return await self.write_back(task.tenant_id, str(promise_id))
        if not task.case_id:
            raise ValueError("task without case_id")
        async with self.lock(f"case:{task.case_id}"):
            return await self._handle_case(task, task.case_id)

    async def _handle_case(self, task: TaskMessage, case_id: str) -> str:
        config = {"configurable": {"thread_id": case_id}}
        ctx = StepContext(
            run_id=task.task_id,
            task_id=task.task_id,
            tenant_id=task.tenant_id,
            case_id=case_id,
        )
        with step_context(ctx):
            snapshot = await self.graph.aget_state(config)
            if snapshot.values and snapshot.values.get("tenant_id") not in (None, task.tenant_id):
                raise PermissionError("task tenant does not own this case")
            if task.kind == "process_case":
                if not snapshot.values:
                    await self.graph.ainvoke(
                        {
                            "case_id": case_id,
                            "tenant_id": task.tenant_id,
                            "run_id": task.task_id,
                        },
                        config,
                    )
                    return "started"
                if snapshot.next and not snapshot.interrupts:
                    await self.graph.ainvoke(None, config)
                    return "recovered"
                return "duplicate"
            if task.kind in ("resume_case", "follow_up"):
                if not snapshot.interrupts:
                    if snapshot.next:
                        # The resumed run crashed mid-node (T-51 review B1): this redelivery
                        # finishes it, re-running the node from its own checks.
                        await self.graph.ainvoke(None, config)
                        return "recovered"
                    return "not_paused"
                pending = snapshot.interrupts[0].value or {}
                expected = (task.resume or {}).get("kind")
                if not expected:
                    raise ValueError("resume without a kind")  # never resume a guessed interrupt
                if pending.get("kind") != expected:
                    log.info(
                        "ignoring resume for %s while paused at %s", expected, pending.get("kind")
                    )
                    return "kind_mismatch"
                await self.graph.ainvoke(Command(resume=task.resume or {}), config)
                return "resumed"
            raise ValueError(f"unknown task kind {task.kind}")

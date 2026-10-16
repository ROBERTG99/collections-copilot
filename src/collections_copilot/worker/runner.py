"""Applies cc.task.v1 messages to the case graph idempotently.

SQS delivers at least once, so every handler first reads the checkpoint:
- process_case: start the graph if no checkpoint exists, continue from the last checkpoint after a
  crash, otherwise ignore the duplicate.
- resume_case: resume only when the graph is paused at an interrupt of the expected kind
  (approval, deferred_send, await_reply); anything else is a stale or duplicate delivery.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import Command

from collections_copilot.integrations.queue.messages import TaskMessage
from collections_copilot.observability.steps import StepContext, step_context

log = logging.getLogger(__name__)


class CaseRunner:
    def __init__(self, graph: Any) -> None:
        self.graph = graph

    async def handle(self, task: TaskMessage) -> str:
        if not task.case_id:
            raise ValueError("task without case_id")
        config = {"configurable": {"thread_id": task.case_id}}
        ctx = StepContext(
            run_id=task.task_id,
            task_id=task.task_id,
            tenant_id=task.tenant_id,
            case_id=task.case_id,
        )
        with step_context(ctx):
            snapshot = await self.graph.aget_state(config)
            if snapshot.values and snapshot.values.get("tenant_id") not in (None, task.tenant_id):
                raise PermissionError("task tenant does not own this case")
            if task.kind == "process_case":
                if not snapshot.values:
                    await self.graph.ainvoke(
                        {
                            "case_id": task.case_id,
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
                    return "not_paused"
                pending = snapshot.interrupts[0].value or {}
                expected = (task.resume or {}).get("kind")
                if expected and pending.get("kind") != expected:
                    log.info(
                        "ignoring resume for %s while paused at %s", expected, pending.get("kind")
                    )
                    return "kind_mismatch"
                await self.graph.ainvoke(Command(resume=task.resume or {}), config)
                return "resumed"
            return "ignored"

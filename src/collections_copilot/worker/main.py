"""Worker entry point: consumes cc-tasks and cc-replies and serves /healthz and /metrics on 8311."""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import AsyncExitStack
from typing import Any

import uvicorn
from langgraph.types import Command
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from collections_copilot.agent.case_graph import build_skeleton_graph
from collections_copilot.agent.checkpointer import open_checkpointer
from collections_copilot.config import Settings, get_settings
from collections_copilot.integrations.queue.messages import TaskMessage
from collections_copilot.integrations.queue.sqs import QueueMessage, SqsQueueClient
from collections_copilot.worker.consumer import Consumer

log = logging.getLogger("collections_copilot.worker")
BIND_ALL = "0.0.0.0"  # noqa: S104 - container port, published only on localhost by compose


def health_app() -> Starlette:
    async def healthz(_: Any) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def metrics(_: Any) -> PlainTextResponse:
        from prometheus_client import generate_latest

        return PlainTextResponse(generate_latest().decode(), media_type="text/plain; version=0.0.4")

    return Starlette(routes=[Route("/healthz", healthz), Route("/metrics", metrics)])


async def run(settings: Settings) -> None:
    async with AsyncExitStack() as stack:
        queues = await stack.enter_async_context(
            SqsQueueClient(settings.aws_region, settings.sqs_endpoint_url)
        )
        saver = await stack.enter_async_context(open_checkpointer(settings.database_url))
        graph = build_skeleton_graph(saver)

        async def handle_task(msg: QueueMessage) -> None:
            task = TaskMessage.model_validate(msg.body)
            config = {"configurable": {"thread_id": task.case_id}}
            if task.kind == "process_case":
                await graph.ainvoke(
                    {"case_id": task.case_id, "tenant_id": task.tenant_id, "status": "queued"},
                    config,
                )
            elif task.kind == "resume_case":
                await graph.ainvoke(Command(resume=task.resume or {}), config)

        consumer = Consumer(
            queues, settings.sqs_tasks_queue, handle_task, concurrency=settings.worker_concurrency
        )
        server = uvicorn.Server(
            uvicorn.Config(
                health_app(), host=BIND_ALL, port=settings.metrics_port, log_level="warning"
            )
        )
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, consumer.stop)
        await asyncio.gather(consumer.run(), server.serve())


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(get_settings()))


if __name__ == "__main__":
    main()

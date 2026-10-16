"""Worker entry point: consumes cc-tasks (case graph) and cc-replies (reply graph), runs the
follow-up scheduler, and serves /healthz and /metrics on 8311."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Iterator
from contextlib import AsyncExitStack
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from collections_copilot.agent.case_graph import build_case_graph
from collections_copilot.agent.checkpointer import open_checkpointer
from collections_copilot.agent.services import DbStepSink, Services
from collections_copilot.agent.store import DbCaseStore
from collections_copilot.config import Settings, get_settings
from collections_copilot.db.session import make_engine, make_sessionmaker
from collections_copilot.integrations.email.smtp import SmtpNotifier
from collections_copilot.integrations.queue.messages import TaskMessage
from collections_copilot.integrations.queue.sqs import QueueMessage, SqsQueueClient
from collections_copilot.integrations.sms import build_sms_sender
from collections_copilot.llm.gateway import LLMGateway
from collections_copilot.llm.providers import build_provider
from collections_copilot.worker.consumer import Consumer
from collections_copilot.worker.runner import CaseRunner

log = logging.getLogger("collections_copilot.worker")
BIND_ALL = "0.0.0.0"  # noqa: S104 - container port, published only on localhost by compose


class _NoSignalServer(uvicorn.Server):
    """uvicorn server that leaves SIGTERM/SIGINT to the worker's shared shutdown handler."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


def health_app() -> Starlette:
    async def healthz(_: Any) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def metrics(_: Any) -> PlainTextResponse:
        from prometheus_client import generate_latest

        return PlainTextResponse(generate_latest().decode(), media_type="text/plain; version=0.0.4")

    return Starlette(routes=[Route("/healthz", healthz), Route("/metrics", metrics)])


def build_services(settings: Settings, sessionmaker: Any) -> Services:
    gateway = LLMGateway.from_settings(
        settings, build_provider(settings), sink=DbStepSink(sessionmaker)
    )
    return Services(
        settings=settings,
        store=DbCaseStore(sessionmaker),
        gateway=gateway,
        sms=build_sms_sender(settings),
        notifier=SmtpNotifier(
            settings.smtp_host, settings.smtp_port, settings.smtp_from, settings.supervisor_email
        ),
    )


async def run(settings: Settings) -> None:
    async with AsyncExitStack() as stack:
        engine = make_engine(settings.database_url)
        stack.push_async_callback(engine.dispose)
        sessionmaker = make_sessionmaker(engine)
        queues = await stack.enter_async_context(
            SqsQueueClient(settings.aws_region, settings.sqs_endpoint_url)
        )
        saver = await stack.enter_async_context(open_checkpointer(settings.database_url))
        services = build_services(settings, sessionmaker)
        runner = CaseRunner(build_case_graph(services, saver))

        async def handle_task(msg: QueueMessage) -> None:
            outcome = await runner.handle(TaskMessage.model_validate(msg.body))
            log.info("task handled", extra={"outcome": outcome, "kind": msg.body.get("kind")})

        consumers = [
            Consumer(
                queues,
                settings.sqs_tasks_queue,
                handle_task,
                concurrency=settings.worker_concurrency,
            )
        ]
        server = _NoSignalServer(
            uvicorn.Config(
                health_app(), host=BIND_ALL, port=settings.metrics_port, log_level="warning"
            )
        )

        def shutdown() -> None:
            for consumer in consumers:
                consumer.stop()
            server.should_exit = True

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, shutdown)
        await asyncio.gather(*(c.run() for c in consumers), server.serve())


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(get_settings()))


if __name__ == "__main__":
    main()

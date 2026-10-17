"""Worker entry point: consumes cc-tasks (case graph) and cc-replies (reply graph), and serves
/healthz and /metrics on 8311. One worker at a time also leads the follow-up scheduler."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Iterator
from contextlib import AsyncExitStack
from typing import Any

import uvicorn
from redis.asyncio import Redis
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from collections_copilot.agent.case_graph import build_case_graph
from collections_copilot.agent.checkpointer import open_checkpointer
from collections_copilot.agent.nodes.record_promise import retry_write_back
from collections_copilot.agent.reply_graph import build_reply_graph
from collections_copilot.agent.scheduler import Scheduler
from collections_copilot.agent.services import DbStepSink, Services
from collections_copilot.agent.store import DbCaseStore
from collections_copilot.config import Settings, get_settings
from collections_copilot.db.session import (
    LeaderLock,
    advisory_lock,
    make_engine,
    make_sessionmaker,
)
from collections_copilot.integrations.email.smtp import SmtpNotifier
from collections_copilot.integrations.mcp import build_ledger
from collections_copilot.integrations.queue.messages import ReplyMessage, TaskMessage
from collections_copilot.integrations.queue.sqs import QueueMessage, SqsQueueClient
from collections_copilot.integrations.sms import build_sms_sender
from collections_copilot.llm.gateway import LLMGateway
from collections_copilot.llm.providers import build_provider
from collections_copilot.observability.events import RedisEventPublisher
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
        redis = Redis.from_url(settings.redis_url)
        stack.push_async_callback(redis.aclose)
        services.events = RedisEventPublisher(redis)
        services.ledger = build_ledger(settings)

        async def enqueue(task: TaskMessage) -> None:
            await queues.send(settings.sqs_tasks_queue, task.model_dump(mode="json", by_alias=True))

        services.enqueue = enqueue
        replies = build_reply_graph(services, sessionmaker)

        async def write_back(tenant_id: str, promise_id: str) -> str:
            return await retry_write_back(services, sessionmaker, tenant_id, promise_id)

        runner = CaseRunner(
            build_case_graph(services, saver),
            lock=lambda key: advisory_lock(sessionmaker, key),
            write_back=write_back,
        )

        async def handle_task(msg: QueueMessage) -> None:
            outcome = await runner.handle(TaskMessage.model_validate(msg.body))
            log.info("task handled", extra={"outcome": outcome, "kind": msg.body.get("kind")})

        async def handle_reply(msg: QueueMessage) -> None:
            reply = ReplyMessage.model_validate(msg.body)
            async with advisory_lock(sessionmaker, f"reply:{reply.message_id}"):
                result = await replies.ainvoke(
                    {"reply": reply.model_dump(mode="json", by_alias=True)}
                )
            log.info("reply handled", extra={"outcome": result.get("outcome")})

        consumers = [
            Consumer(
                queues,
                settings.sqs_tasks_queue,
                handle_task,
                concurrency=settings.worker_concurrency,
            ),
            Consumer(
                queues,
                settings.sqs_replies_queue,
                handle_reply,
                concurrency=settings.worker_concurrency,
            ),
        ]
        stop = asyncio.Event()

        async def enqueue_reply(reply: ReplyMessage) -> None:
            await queues.send(
                settings.sqs_replies_queue, reply.model_dump(mode="json", by_alias=True)
            )

        scheduler = Scheduler(
            sessionmaker,
            LeaderLock(engine, "cc-scheduler"),
            enqueue,
            services.notifier,
            enqueue_reply=enqueue_reply,
        )
        server = _NoSignalServer(
            uvicorn.Config(
                health_app(), host=BIND_ALL, port=settings.metrics_port, log_level="warning"
            )
        )

        def shutdown() -> None:
            stop.set()
            for consumer in consumers:
                consumer.stop()
            server.should_exit = True

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, shutdown)
        await asyncio.gather(
            *(c.run() for c in consumers),
            scheduler.run(settings.scheduler_interval_s, stop),
            server.serve(),
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(get_settings()))


if __name__ == "__main__":
    main()

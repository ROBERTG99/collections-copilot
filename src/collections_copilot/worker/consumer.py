"""Generic SQS consumer: long polling, bounded concurrency, visibility extension, ack on success."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from collections_copilot.integrations.queue.sqs import QueueClient, QueueMessage

log = logging.getLogger(__name__)

Handler = Callable[[QueueMessage], Awaitable[None]]


class Consumer:
    def __init__(
        self,
        queue_client: QueueClient,
        queue: str,
        handler: Handler,
        concurrency: int = 8,
        wait_seconds: int = 20,
        visibility_extend_every_s: float = 30.0,
        visibility_timeout_s: int = 60,
    ) -> None:
        self.queue_client = queue_client
        self.queue = queue
        self.handler = handler
        self.sem = asyncio.Semaphore(concurrency)
        self.wait_seconds = wait_seconds
        self.extend_every = visibility_extend_every_s
        self.visibility_timeout = visibility_timeout_s
        self._stop = asyncio.Event()
        self._tasks: set[asyncio.Task[Any]] = set()
        self.paused: Callable[[], Awaitable[bool]] | None = None

    def stop(self) -> None:
        self._stop.set()

    async def _keepalive(self, msg: QueueMessage) -> None:
        while True:
            await asyncio.sleep(self.extend_every)
            with contextlib.suppress(Exception):
                await self.queue_client.extend_visibility(
                    self.queue, msg.receipt_handle, self.visibility_timeout
                )

    async def _process(self, msg: QueueMessage) -> None:
        keepalive = asyncio.create_task(self._keepalive(msg))
        try:
            await self.handler(msg)
        except Exception:
            # Leave the message: it becomes visible again and, after maxReceiveCount, goes to the DLQ.
            log.exception(
                "handler failed", extra={"queue": self.queue, "message_id": msg.message_id}
            )
        else:
            await self.queue_client.delete(self.queue, msg.receipt_handle)
        finally:
            keepalive.cancel()
            self.sem.release()

    async def run(self) -> None:
        while not self._stop.is_set():
            if self.paused is not None and await self.paused():
                await asyncio.sleep(1)
                continue
            free = self.sem._value  # number of free slots
            if free == 0:
                await asyncio.sleep(0.05)
                continue
            try:
                messages = await self.queue_client.receive(
                    self.queue, max_messages=min(10, free), wait_seconds=self.wait_seconds
                )
            except Exception:
                log.warning("receive failed; backing off", extra={"queue": self.queue})
                await asyncio.sleep(1)
                continue
            for msg in messages:
                await self.sem.acquire()
                task = asyncio.create_task(self._process(msg))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

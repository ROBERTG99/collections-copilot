"""QueueClient interface and the SQS adapter (aiobotocore; ElasticMQ locally, moto in unit tests)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Protocol

from aiobotocore.session import get_session

from collections_copilot.observability import tracing


@dataclass(frozen=True)
class QueueMessage:
    message_id: str
    receipt_handle: str
    body: dict[str, Any]
    receive_count: int = 1
    attributes: dict[str, str] = field(default_factory=dict)


class QueueUnavailableError(RuntimeError):
    """The queue service cannot be reached (maps to 503 + Retry-After at the API)."""


class QueueClient(Protocol):
    async def send(
        self, queue: str, body: Mapping[str, Any], attributes: Mapping[str, str] | None = None
    ) -> str: ...

    async def receive(
        self, queue: str, max_messages: int = 10, wait_seconds: int = 20
    ) -> list[QueueMessage]: ...

    async def delete(self, queue: str, receipt_handle: str) -> None: ...

    async def extend_visibility(self, queue: str, receipt_handle: str, seconds: int) -> None: ...

    async def depth(self, queue: str) -> int: ...


class SqsQueueClient:
    def __init__(self, region: str, endpoint_url: str | None = None) -> None:
        self.region = region
        self.endpoint_url = endpoint_url
        self._stack = AsyncExitStack()
        self._client: Any = None
        self._urls: dict[str, str] = {}

    async def __aenter__(self) -> SqsQueueClient:
        session = get_session()
        self._client = await self._stack.enter_async_context(
            session.create_client("sqs", region_name=self.region, endpoint_url=self.endpoint_url)
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()

    async def url(self, queue: str) -> str:
        if queue not in self._urls:
            try:
                resp = await self._client.get_queue_url(QueueName=queue)
            except Exception as exc:
                raise QueueUnavailableError(str(exc)) from exc
            self._urls[queue] = resp["QueueUrl"]
        return self._urls[queue]

    async def send(
        self, queue: str, body: Mapping[str, Any], attributes: Mapping[str, str] | None = None
    ) -> str:
        carrier = tracing.inject(dict(attributes or {}))  # §14: the trace crosses the queue
        attrs = {k: {"DataType": "String", "StringValue": v} for k, v in carrier.items()}
        try:
            resp = await self._client.send_message(
                QueueUrl=await self.url(queue),
                MessageBody=json.dumps(body, default=str),
                MessageAttributes=attrs,
            )
        except QueueUnavailableError:
            raise
        except Exception as exc:
            raise QueueUnavailableError(str(exc)) from exc
        return str(resp["MessageId"])

    async def receive(
        self, queue: str, max_messages: int = 10, wait_seconds: int = 20
    ) -> list[QueueMessage]:
        resp = await self._client.receive_message(
            QueueUrl=await self.url(queue),
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=wait_seconds,
            AttributeNames=["ApproximateReceiveCount"],
            MessageAttributeNames=["All"],
        )
        out = []
        for m in resp.get("Messages", []):
            attrs = {
                k: v.get("StringValue", "") for k, v in (m.get("MessageAttributes") or {}).items()
            }
            try:
                body = json.loads(m["Body"])
                if not isinstance(body, dict):
                    raise ValueError("body is not an object")
            except ValueError:
                # Poison message: the handler rejects it and it reaches the DLQ on its own,
                # without failing the rest of the batch.
                body = {"__invalid__": True}
            out.append(
                QueueMessage(
                    message_id=m["MessageId"],
                    receipt_handle=m["ReceiptHandle"],
                    body=body,
                    receive_count=int(m.get("Attributes", {}).get("ApproximateReceiveCount", "1")),
                    attributes=attrs,
                )
            )
        return out

    async def delete(self, queue: str, receipt_handle: str) -> None:
        await self._client.delete_message(
            QueueUrl=await self.url(queue), ReceiptHandle=receipt_handle
        )

    async def extend_visibility(self, queue: str, receipt_handle: str, seconds: int) -> None:
        await self._client.change_message_visibility(
            QueueUrl=await self.url(queue), ReceiptHandle=receipt_handle, VisibilityTimeout=seconds
        )

    async def depth(self, queue: str) -> int:
        resp = await self._client.get_queue_attributes(
            QueueUrl=await self.url(queue), AttributeNames=["ApproximateNumberOfMessages"]
        )
        return int(resp["Attributes"].get("ApproximateNumberOfMessages", "0"))

    async def purge(self, queue: str) -> None:
        await self._client.purge_queue(QueueUrl=await self.url(queue))

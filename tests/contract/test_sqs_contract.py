"""QueueClient contract (§7.4): the SQS adapter behaves the same against ElasticMQ and moto."""

from __future__ import annotations

import uuid

import pytest

from collections_copilot.integrations.queue.sqs import SqsQueueClient

pytestmark = pytest.mark.contract


@pytest.fixture(params=["elasticmq", "moto"])
def endpoint(request: pytest.FixtureRequest, moto_url: str) -> str:
    return "http://localhost:8340" if request.param == "elasticmq" else moto_url


async def test_send_receive_delete_round_trip(endpoint: str) -> None:
    queue = f"contract-{uuid.uuid4().hex[:8]}"
    async with SqsQueueClient("us-east-1", endpoint) as client:
        await client.create(queue)
        try:
            body = {"task_id": "t-1", "kind": "process_case", "note": "ñ € 😀"}
            await client.send(queue, body)
            assert await client.depth(queue) == 1
            [msg] = await client.receive(queue, max_messages=10, wait_seconds=1)
            assert msg.body == body  # JSON survives unchanged, Unicode included
            assert msg.receive_count == 1
            # Hidden while in flight, visible again when the visibility is reset (redelivery).
            assert await client.receive(queue, max_messages=10, wait_seconds=0) == []
            await client.extend_visibility(queue, msg.receipt_handle, 0)
            [again] = await client.receive(queue, max_messages=10, wait_seconds=1)
            assert again.body == body
            assert again.receive_count == 2
            await client.delete(queue, again.receipt_handle)
            assert await client.depth(queue) == 0
        finally:
            await client.remove(queue)


async def test_trace_context_travels_in_message_attributes(endpoint: str) -> None:
    from collections_copilot.observability import tracing

    tracing.install_test_exporter()  # a real tracer, so a span context exists to inject
    queue = f"contract-{uuid.uuid4().hex[:8]}"
    async with SqsQueueClient("us-east-1", endpoint) as client:
        await client.create(queue)
        try:
            with tracing.span("enqueue"):
                await client.send(queue, {"kind": "probe"})
            [msg] = await client.receive(queue, max_messages=1, wait_seconds=1)
            assert "traceparent" in msg.attributes
        finally:
            await client.remove(queue)

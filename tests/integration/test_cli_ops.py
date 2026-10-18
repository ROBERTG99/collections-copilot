"""Operations commands from the runbook and the replay-dlq skill: `cc dlq peek`, `cc replay-dlq`
(BUILD.md §6 skills) and `cc dev-token` (`make dev-token`)."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest
from typer.testing import CliRunner

from collections_copilot.cli import app
from collections_copilot.config import Settings
from collections_copilot.integrations.queue.sqs import QueueMessage, SqsQueueClient

pytestmark = pytest.mark.integration
SECRET_BODY = "I can pay 1,200.00 on Friday"  # a reply body must never be printed


async def _queues(source: str, dlq: str) -> None:
    settings = Settings()
    async with SqsQueueClient(settings.aws_region, settings.sqs_endpoint_url) as client:
        await client.create(source)
        await client.create(dlq)
        for i in range(3):
            await client.send(dlq, _original(i))


def _original(i: int) -> dict[str, Any]:
    return {"task_id": f"t-{i}", "kind": "process_case", "tenant_id": "x", "body": SECRET_BODY}


async def _drain(source: str, dlq: str) -> tuple[list[QueueMessage], int]:
    settings = Settings()
    async with SqsQueueClient(settings.aws_region, settings.sqs_endpoint_url) as client:
        moved = await client.receive(source, max_messages=10, wait_seconds=1)
        left = await client.depth(dlq)
        for queue in (source, dlq):
            await client.remove(queue)
    return moved, left


async def test_peek_then_replay_moves_messages_back_unchanged() -> None:
    source = f"cc-ops-{uuid.uuid4().hex[:8]}"
    dlq = f"{source}-dlq"
    await _queues(source, dlq)
    runner = CliRunner()

    peek = await asyncio.to_thread(runner.invoke, app, ["dlq", "peek", "--queue", dlq])
    assert peek.exit_code == 0, peek.output
    assert peek.output.count("kind=process_case") == 3
    assert "task_id=t-0" in peek.output
    assert peek.output.count("msg_id=") == 3  # the id the worker logs carry
    assert SECRET_BODY not in peek.output  # ids only, never bodies

    replay = await asyncio.to_thread(
        runner.invoke, app, ["replay-dlq", "--queue", dlq, "--max", "2"]
    )
    assert replay.exit_code == 0, replay.output
    assert f"replayed=2 from={dlq} to={source}" in replay.output

    moved, left = await _drain(source, dlq)
    assert (len(moved), left) == (2, 1)  # peek left every message in place
    originals = [_original(i) for i in range(3)]
    for m in moved:  # byte for byte
        assert m.body in originals
        assert m.raw == json.dumps(m.body)


async def test_replay_keeps_a_body_that_is_not_json() -> None:
    source = f"cc-ops-{uuid.uuid4().hex[:8]}"
    dlq = f"{source}-dlq"
    settings = Settings()
    async with SqsQueueClient(settings.aws_region, settings.sqs_endpoint_url) as client:
        await client.create(source)
        await client.create(dlq)
        await client.send_raw(dlq, "not json {", {"origin": "test"})

    replay = await asyncio.to_thread(CliRunner().invoke, app, ["replay-dlq", "--queue", dlq])
    assert replay.exit_code == 0, replay.output

    moved, left = await _drain(source, dlq)
    assert left == 0
    assert [(m.raw, m.body, m.attributes) for m in moved] == [
        ("not json {", {"__invalid__": True}, {"origin": "test"})
    ]


async def test_peek_shows_the_reply_ids_of_a_reply_message() -> None:
    dlq = f"cc-ops-{uuid.uuid4().hex[:8]}-dlq"
    settings = Settings()
    async with SqsQueueClient(settings.aws_region, settings.sqs_endpoint_url) as client:
        await client.create(dlq)
        await client.send(
            dlq,
            {
                "schema": "cc.reply.v1",
                "tenant_id": "x",
                "message_id": "m-1",
                "provider_sid": "SM1",
                "from_e164": "+15550000001",
                "body": SECRET_BODY,
            },
        )
    peek = await asyncio.to_thread(CliRunner().invoke, app, ["dlq", "peek", "--queue", dlq])
    async with SqsQueueClient(settings.aws_region, settings.sqs_endpoint_url) as client:
        await client.remove(dlq)
    assert peek.exit_code == 0, peek.output
    assert "reply=m-1 sid=SM1" in peek.output
    assert SECRET_BODY not in peek.output
    assert "+1555" not in peek.output


def test_replay_needs_a_dead_letter_queue() -> None:
    result = CliRunner().invoke(app, ["replay-dlq", "--queue", "cc-tasks"])
    assert result.exit_code == 2
    assert "-dlq" in result.output


def test_dev_token_refuses_where_agent_tokens_are_kms_signed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Production settings with dev defaults do not load at all (Settings validation).
    from collections_copilot.config import get_settings

    monkeypatch.setenv("CC_AGENT_TOKEN_PROVIDER", "kms")
    monkeypatch.setenv("CC_AGENT_KMS_KEY_ID", "alias/cc-agent")
    get_settings.cache_clear()
    try:
        result = CliRunner().invoke(app, ["dev-token"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 2, result.output
    assert "local dev" in result.output


def test_dev_token_prints_a_gateway_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from collections_copilot.integrations.mcp import tokens

    seen: dict[str, Any] = {}

    async def fake_token(self: Any, tenant: str, scopes: list[str]) -> str:
        seen.update(issuer=self.issuer_url, tenant=tenant, scopes=scopes, sub=self.subject)
        return "header.payload.signature"

    monkeypatch.setattr(tokens.DevIssuerTokenProvider, "token", fake_token)
    result = CliRunner().invoke(app, ["dev-token", "--tenant", "t-1", "--scope", "ledger:read"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "header.payload.signature"
    assert seen == {
        "issuer": Settings().mcpgw_issuer_url,
        "tenant": "t-1",
        "scopes": ["ledger:read"],
        "sub": "cc-worker",
    }

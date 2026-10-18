"""Operations commands from the runbook and the replay-dlq skill: `cc dlq peek`, `cc replay-dlq`
(BUILD.md §6 skills) and `cc dev-token` (`make dev-token`)."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from typer.testing import CliRunner

from collections_copilot.cli import app
from collections_copilot.config import Settings
from collections_copilot.integrations.queue.sqs import SqsQueueClient

pytestmark = pytest.mark.integration
SECRET_BODY = "I can pay 1,200.00 on Friday"  # a reply body must never be printed


async def _queues(source: str, dlq: str) -> None:
    settings = Settings()
    async with SqsQueueClient(settings.aws_region, settings.sqs_endpoint_url) as client:
        await client.create(source)
        await client.create(dlq)
        for i in range(3):
            await client.send(
                dlq,
                {
                    "task_id": f"t-{i}",
                    "kind": "process_case",
                    "tenant_id": "x",
                    "body": SECRET_BODY,
                },
            )


async def _depths(source: str, dlq: str) -> tuple[int, int, list[dict[str, Any]]]:
    settings = Settings()
    async with SqsQueueClient(settings.aws_region, settings.sqs_endpoint_url) as client:
        moved = await client.receive(source, max_messages=10, wait_seconds=1)
        left = await client.depth(dlq)
        for queue in (source, dlq):
            await client.remove(queue)
    return len(moved), left, [m.body for m in moved]


async def test_peek_then_replay_moves_messages_back_unchanged() -> None:
    source = f"cc-ops-{uuid.uuid4().hex[:8]}"
    dlq = f"{source}-dlq"
    await _queues(source, dlq)
    runner = CliRunner()

    peek = await asyncio.to_thread(runner.invoke, app, ["dlq", "peek", "--queue", dlq])
    assert peek.exit_code == 0, peek.output
    assert peek.output.count("kind=process_case") == 3
    assert "task_id=t-0" in peek.output
    assert SECRET_BODY not in peek.output  # ids only, never bodies

    replay = await asyncio.to_thread(
        runner.invoke, app, ["replay-dlq", "--queue", dlq, "--max", "2"]
    )
    assert replay.exit_code == 0, replay.output
    assert f"replayed=2 from={dlq} to={source}" in replay.output

    moved, left, bodies = await _depths(source, dlq)
    assert (moved, left) == (2, 1)  # peek left every message in place
    assert {b["task_id"] for b in bodies} <= {"t-0", "t-1", "t-2"}  # same task ids: idempotent


def test_replay_needs_a_dead_letter_queue() -> None:
    result = CliRunner().invoke(app, ["replay-dlq", "--queue", "cc-tasks"])
    assert result.exit_code == 2
    assert "-dlq" in result.output


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

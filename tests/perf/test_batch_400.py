"""Offline performance budgets on Postgres and ElasticMQ, with the FakeLLM.

NFR-4: 400 accounts reach their approval interrupt (or another settled state) in under 3 min with
4 workers. Each worker is one Consumer with one slot and its own checkpointer pool, reading a fresh
queue the compose worker does not read. The four share this process's event loop, so the run is a
lower bound on four worker processes, and its per-case times include waiting for the loop.

NFR-2: one case graph, run alone, reaches its approval interrupt with p95 under 300 ms.

Each test uses gen.v1 (seed 4242) under a new tenant name: the seeded accounts, shared with no
other test.
"""

from __future__ import annotations

import asyncio
import statistics
import time
import uuid
from contextlib import AsyncExitStack
from typing import Any

import pytest

from collections_copilot.agent.case_graph import build_case_graph
from collections_copilot.agent.checkpointer import open_checkpointer
from collections_copilot.config import Settings
from collections_copilot.db.repositories import Repos
from collections_copilot.integrations.queue.messages import TaskMessage
from collections_copilot.integrations.queue.sqs import QueueMessage, SqsQueueClient
from collections_copilot.worker.consumer import Consumer
from collections_copilot.worker.runner import CaseRunner
from data.generator.gen import DEFAULT_SEED, generate_tenant
from data.generator.load import load_tenant
from tests.integration.graph_support import make_services

pytestmark = [pytest.mark.perf, pytest.mark.integration]

ACCOUNTS = 400
WORKERS = 4
BUDGET_S = 180.0
UNSETTLED = {"queued", "processing"}
CASES_NFR2 = 100
P95_BUDGET_MS = 300.0


async def _statuses(sm: Any, tenant: uuid.UUID, case_ids: list[str]) -> dict[str, str]:
    async with sm() as s:
        repos = Repos(s)
        out = {}
        for case_id in case_ids:
            case = await repos.cases.get(tenant, case_id)
            assert case is not None
            out[case_id] = case.status
        return out


async def _queued_cases(sm: Any, accounts: int) -> tuple[uuid.UUID, list[str]]:
    spec = generate_tenant(f"perf-{uuid.uuid4().hex[:8]}", accounts, DEFAULT_SEED, "P")
    assert await load_tenant(sm, spec)
    async with sm() as s, s.begin():
        repos = Repos(s)
        account_ids = await repos.accounts.list_ids(spec.tenant_id)
        case_ids = [
            str((await repos.cases.create(spec.tenant_id, a, status="queued")).id)
            for a in account_ids
        ]
    assert len(case_ids) == accounts
    return spec.tenant_id, case_ids


def p95(values: list[float]) -> float:
    return statistics.quantiles(values, n=20)[-1]


async def test_case_graph_reaches_its_interrupt_within_300ms_p95(
    sessionmaker, database_url
) -> None:  # type: ignore[no-untyped-def]
    tenant, case_ids = await _queued_cases(sessionmaker, CASES_NFR2)
    durations: list[float] = []
    async with open_checkpointer(database_url, max_size=2) as saver:
        runner = CaseRunner(build_case_graph(make_services(sessionmaker, database_url), saver))
        for case_id in case_ids:
            started = time.perf_counter()
            await runner.handle(
                TaskMessage(tenant_id=str(tenant), kind="process_case", case_id=case_id)
            )
            durations.append((time.perf_counter() - started) * 1000)
    statuses = await _statuses(sessionmaker, tenant, case_ids)
    print(
        f"\ncase_graph: {len(durations)} cases one at a time, p50 {statistics.median(durations):.0f} ms, "
        f"p95 {p95(durations):.0f} ms (budget {P95_BUDGET_MS:.0f} ms)"
    )
    assert UNSETTLED.isdisjoint(statuses.values())
    assert p95(durations) < P95_BUDGET_MS


async def test_400_accounts_reach_approval_within_budget(sessionmaker, database_url) -> None:  # type: ignore[no-untyped-def]
    tenant, case_ids = await _queued_cases(sessionmaker, ACCOUNTS)

    settings = Settings()
    queue = f"cc-perf-{uuid.uuid4().hex[:8]}"
    durations: list[float] = []
    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(
            SqsQueueClient(settings.aws_region, settings.sqs_endpoint_url)
        )
        await client._client.create_queue(QueueName=queue)
        for case_id in case_ids:
            task = TaskMessage(tenant_id=str(tenant), kind="process_case", case_id=case_id)
            await client.send(queue, task.model_dump(mode="json", by_alias=True))

        consumers = []
        for _ in range(WORKERS):
            saver = await stack.enter_async_context(open_checkpointer(database_url, max_size=2))
            runner = CaseRunner(build_case_graph(make_services(sessionmaker, database_url), saver))

            async def handle(msg: QueueMessage, runner: CaseRunner = runner) -> None:
                started = time.perf_counter()
                await runner.handle(TaskMessage.model_validate(msg.body))
                durations.append(time.perf_counter() - started)

            consumers.append(Consumer(client, queue, handle, concurrency=1, wait_seconds=1))

        start = time.perf_counter()
        runs = [asyncio.create_task(c.run()) for c in consumers]
        statuses: dict[str, str] = {}
        try:
            while time.perf_counter() - start < BUDGET_S:
                statuses = await _statuses(sessionmaker, tenant, case_ids)
                if not UNSETTLED & set(statuses.values()):
                    break
                await asyncio.sleep(0.5)
            elapsed = time.perf_counter() - start
        finally:
            for c in consumers:
                c.stop()
            await asyncio.gather(*runs)
            await client._client.delete_queue(QueueUrl=await client.url(queue))

    counts: dict[str, int] = {}
    for status in statuses.values():
        counts[status] = counts.get(status, 0) + 1
    print(
        f"\nbatch_400: {ACCOUNTS} cases, {WORKERS} workers, {elapsed:.1f}s (budget {BUDGET_S:.0f}s), "
        f"per-case p95 {p95(durations) * 1000:.0f} ms with 4 sharing one event loop, statuses {counts}"
    )
    assert elapsed < BUDGET_S
    assert UNSETTLED.isdisjoint(counts), counts
    assert "failed" not in counts, counts
    assert counts.get("awaiting_approval", 0) > 0

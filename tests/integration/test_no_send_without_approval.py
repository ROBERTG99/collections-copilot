"""T-27 / NFR-5: no SMS leaves without an approval record for the exact draft version.

Static: only two call sites may hand text to an SMS provider (the case graph's `send_sms` and
the STOP confirmation). Dynamic: every way of resuming or auto-approving a case without a
stored approval ends with zero messages sent.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path
from typing import Any

import pytest
from langgraph.types import Command

from collections_copilot.db.repositories import Repos
from collections_copilot.integrations.queue.messages import TaskMessage
from collections_copilot.integrations.sms.base import InMemorySmsSender
from collections_copilot.llm.providers.base import ProviderRequest, ProviderResponse
from collections_copilot.llm.providers.fake import FakeLLM, FakeMode
from collections_copilot.worker.runner import CaseRunner
from tests.factories import make_account
from tests.integration.graph_support import case_graph, config, make_services, new_case

pytestmark = pytest.mark.integration
SRC = Path(__file__).resolve().parents[2] / "src" / "collections_copilot"
PLAN_NOTE = "Owner asked to split the balance into installments; cash flow is tight this quarter."
AP_NOTE = "AP contact confirmed the invoice was received and is in the approval queue."
ALLOWED_SENDERS = {
    ("agent/case_graph.py", "send_sms"),
    ("agent/reply_actions.py", "_send_confirmation"),
}


class _SendSites(ast.NodeVisitor):
    """Records (file, innermost enclosing function) for every `<...sms...>.send(...)` call."""

    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.stack: list[str] = []
        self.sites: set[tuple[str, str]] = set()

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "send"
            and "sms" in ast.unparse(func.value).lower()
        ):
            self.sites.add((self.rel, self.stack[-1] if self.stack else "<module>"))
        self.generic_visit(node)


def sms_send_sites() -> set[tuple[str, str]]:
    sites: set[tuple[str, str]] = set()
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("integrations/sms/"):
            continue  # the adapters themselves
        visitor = _SendSites(rel)
        visitor.visit(ast.parse(path.read_text()))
        sites |= visitor.sites
    return sites


def test_only_two_code_paths_call_the_sms_provider() -> None:
    assert sms_send_sites() == ALLOWED_SENDERS


def nba(action: str, confidence: float) -> type[FakeLLM]:
    class Fixed(FakeLLM):
        async def complete(self, request: ProviderRequest) -> ProviderResponse:
            if request.route == "next_best_action":
                body = (
                    f'{{"action": "{action}", "rationale": "Synthetic.", '
                    f'"confidence": {confidence}, "cited_note_ids": []}}'
                )
                return ProviderResponse(body, "end_turn", "fake")
            return await super().complete(request)

    return Fixed


async def run_case(
    sm: Any,
    database_url: str,
    tenant: uuid.UUID,
    llm: FakeLLM,
    sms: InMemorySmsSender,
    amount_cents: int = 30_000,
    note: str = AP_NOTE,
) -> tuple[str, Any]:
    account = await make_account(
        sm, tenant, notes=[note], invoices=[{"amount_cents": amount_cents, "days_past_due": 20}]
    )
    case_id = await new_case(sm, tenant, account)
    async with case_graph(make_services(sm, database_url, llm, sms), database_url) as graph:
        await graph.ainvoke({"case_id": case_id, "tenant_id": str(tenant)}, config(case_id))
        return case_id, await graph.aget_state(config(case_id))


async def test_resume_without_record_sends_nothing(sessionmaker, database_url, tenant) -> None:  # type: ignore[no-untyped-def]
    sms = InMemorySmsSender()
    case_id, _ = await run_case(sessionmaker, database_url, tenant, FakeLLM(), sms, note=PLAN_NOTE)
    async with case_graph(
        make_services(sessionmaker, database_url, sms=sms), database_url
    ) as graph:
        for decision in ("approve", "edit"):
            await graph.ainvoke(
                Command(resume={"kind": "approval", "decision": decision, "body": "Pay now."}),
                config(case_id),
            )
        runner = CaseRunner(graph)
        task = TaskMessage(
            tenant_id=str(tenant),
            kind="resume_case",
            case_id=case_id,
            resume={"kind": "approval", "decision": "approve", "actor": "mallory"},
        )
        assert await runner.handle(task) == "resumed"
        snapshot = await graph.aget_state(config(case_id))
    assert sms.sent == []
    assert snapshot.interrupts[0].value["kind"] == "approval"


@pytest.mark.parametrize(
    ("llm", "amount_cents"),
    [
        (nba("payment_plan", 0.99)(), 30_000),  # only `remind` is ever auto-approved
        (nba("remind", 0.89)(), 30_000),  # under the confidence floor
        (nba("remind", 0.99)(), 50_001),  # above USD 500
        (FakeLLM(mode=FakeMode(outage=True)), 30_000),  # rules fallback is never auto-approved
    ],
    ids=["payment_plan_0.99", "confidence_0.89", "usd_500.01", "fallback"],
)
async def test_auto_approval_negatives_wait_for_a_human(
    sessionmaker, database_url, tenant, llm: FakeLLM, amount_cents: int
) -> None:  # type: ignore[no-untyped-def]
    sms = InMemorySmsSender()
    case_id, snapshot = await run_case(sessionmaker, database_url, tenant, llm, sms, amount_cents)
    assert sms.sent == []
    if snapshot.interrupts:
        assert snapshot.interrupts[0].value["kind"] == "approval"
    async with sessionmaker() as s:
        assert await Repos(s).approvals.for_case(tenant, uuid.UUID(case_id)) == []


async def test_tenant_tightening_applies(sessionmaker, database_url, tenant) -> None:  # type: ignore[no-untyped-def]
    async with sessionmaker() as s, s.begin():
        await Repos(s).settings.put(
            tenant,
            "auto_approval",
            {"enabled": True, "max_usd": 500, "min_confidence": 0.99},
            "sam",
        )
    sms = InMemorySmsSender()
    _, snapshot = await run_case(sessionmaker, database_url, tenant, nba("remind", 0.95)(), sms)
    assert sms.sent == []
    assert snapshot.interrupts[0].value["kind"] == "approval"


async def test_matching_policy_auto_approves_with_record(
    sessionmaker, database_url, tenant
) -> None:  # type: ignore[no-untyped-def]
    sms = InMemorySmsSender()
    case_id, _ = await run_case(sessionmaker, database_url, tenant, nba("remind", 0.95)(), sms)
    assert len(sms.sent) == 1
    async with sessionmaker() as s:
        [approval] = await Repos(s).approvals.for_case(tenant, uuid.UUID(case_id))
    assert approval.decision == "auto_approve"
    assert approval.policy_snapshot["max_usd"] == 500

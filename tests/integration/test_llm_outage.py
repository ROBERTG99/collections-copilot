"""NFR-11: an LLM outage degrades to rules and templates labelled `fallback`, never to unapproved sends."""

from __future__ import annotations

import uuid

import pytest

from collections_copilot.db.repositories import Repos
from collections_copilot.integrations.sms.base import InMemorySmsSender
from collections_copilot.llm.providers.fake import FakeLLM, FakeMode
from tests.factories import make_account
from tests.integration.graph_support import case_graph, config, make_services, new_case

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "mode",
    [FakeMode(outage=True), FakeMode(refusal=frozenset({"next_best_action", "draft_message"}))],
)
async def test_outage_falls_back_and_waits_for_a_human(
    sessionmaker, database_url, tenant, mode: FakeMode
) -> None:  # type: ignore[no-untyped-def]
    account = await make_account(
        sessionmaker,
        tenant,
        notes=["Please call the AP desk on Tuesday."],
        invoices=[{"amount_cents": 20_000, "days_past_due": 15}],
    )
    case_id = await new_case(sessionmaker, tenant, account)
    sms = InMemorySmsSender()
    async with case_graph(
        make_services(sessionmaker, database_url, FakeLLM(mode=mode), sms), database_url
    ) as graph:
        await graph.ainvoke({"case_id": case_id, "tenant_id": str(tenant)}, config(case_id))
        snapshot = await graph.aget_state(config(case_id))
    assert snapshot.next == (
        "human_approval",
    )  # small remind, but fallback output is never auto-approved
    assert snapshot.values["nba"]["source"] == "fallback"
    assert snapshot.values["draft"]["author"] == "fallback"
    assert snapshot.values["fallback"] is True
    assert sms.sent == []
    async with sessionmaker() as s:
        repos = Repos(s)
        case = await repos.cases.get(tenant, case_id)
        assert case is not None
        assert case.fallback is True
        assert case.status == "awaiting_approval"
        [draft] = await repos.drafts.for_case(tenant, uuid.UUID(case_id))
        assert draft.author == "fallback"
        assert draft.postcheck is not None
        assert draft.postcheck["ok"] is True

"""T-51 review M1: an analyst's edit stands when auto-approval starts to match before the resume.

On resume, `human_approval` re-runs from its start. If the auto-approval policy now matches (a
supervisor re-enabled it, or the balance fell under the ceiling), the old version must not be
auto-approved over the human's decision on the new version.
"""

from __future__ import annotations

import uuid
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from collections_copilot.agent.case_graph import build_case_graph
from collections_copilot.agent.services import Services
from collections_copilot.agent.store import CaseFacts, InvoiceRow, MemoryCaseStore, NoteFact
from collections_copilot.config import get_settings
from collections_copilot.integrations.sms.base import InMemorySmsSender
from collections_copilot.llm.gateway import LLMGateway
from collections_copilot.llm.providers.base import ProviderRequest, ProviderResponse
from collections_copilot.llm.providers.fake import FakeLLM

NOW = "2026-10-14T15:00:00+00:00"  # 11:00 in New York


class ConfidentRemind(FakeLLM):
    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        if request.route == "next_best_action":
            body = '{"action": "remind", "rationale": "Synthetic.", "confidence": 0.95, "cited_note_ids": []}'
            return ProviderResponse(body, "end_turn", "fake")
        return await super().complete(request)


async def test_an_edit_is_sent_even_if_the_policy_now_matches() -> None:
    settings = get_settings()
    sms, store = InMemorySmsSender(), MemoryCaseStore()
    gateway = LLMGateway.from_settings(settings, ConfidentRemind())
    graph = build_case_graph(
        Services(settings=settings, store=store, gateway=gateway, sms=sms), InMemorySaver()
    )
    case_id, tenant_id = str(uuid.uuid4()), str(uuid.uuid4())
    cfg: Any = {"configurable": {"thread_id": case_id}}
    facts = CaseFacts(
        tenant_id=tenant_id,
        case_id=case_id,
        account_id=str(uuid.uuid4()),
        external_ref="A1",
        name="Synthetic Co",
        phone_e164="+15550001111",
        timezone="America/New_York",
        segment="smb",
        language="en",
        currency="USD",
        opted_out=False,
        contact_flag=None,
        invoices=[InvoiceRow("INV-1", 30_000, "USD", "open", 20)],
        notes=[NoteFact(str(uuid.uuid4()), "crm", "AP confirmed the invoice is in the queue.")],
        broken_promises_90d=0,
        contacts_7d=[],
        auto_policy={"enabled": False},  # a supervisor turned auto-approval off
    )
    await graph.ainvoke(
        {"case_id": case_id, "tenant_id": tenant_id, "now": NOW, "input_facts": facts.as_state()},
        cfg,
    )
    snap = await graph.aget_state(cfg)
    assert snap.interrupts[0].value["kind"] == "approval"

    # The analyst edits (what POST /approval does): v1 superseded, v2 approved by an edit.
    v1 = store.drafts[(case_id, 1)]
    v1["status"] = "superseded"
    edited = v1["body"].replace("Synthetic Co", "Synthetic Co (edited)")
    store.drafts[(case_id, 2)] = {
        **v1, "version": 2, "body": edited, "author": "analyst", "status": "approved"
    }  # fmt: skip
    store.approve(case_id, 2, "edit", "ana")
    # Meanwhile the supervisor turned auto-approval back on.
    fresh = dict(snap.values["facts"])
    fresh["auto_policy"] = {"enabled": True}
    await graph.aupdate_state(cfg, {"facts": fresh})

    out = await graph.ainvoke(
        Command(resume={"kind": "approval", "decision": "edit", "draft_version": 2}), cfg
    )
    assert out["approval"]["decision"] == "edit"
    assert [m["body"] for m in sms.sent] == [edited]

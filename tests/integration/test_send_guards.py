"""M2 review regressions (FR-5, FR-6, NFR-5): the send path re-reads the database.

Between an approval and the actual send (a deferred 08:00 send, or a resume hours later) the
debtor may dispute, opt out or be flagged, the case may be closed and the draft cancelled. The
send uses fresh facts, the stored draft body and the stored approval record for that version.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from langgraph.types import Command
from sqlalchemy import delete, select, update

from collections_copilot.agent.reply_actions import apply_stop, cancel_open_work
from collections_copilot.agent.store import DbCaseStore
from collections_copilot.db.models import Approval, AuditEvent, ContactLog, Draft
from collections_copilot.db.repositories import Repos
from collections_copilot.integrations.sms.base import InMemorySmsSender
from collections_copilot.llm.providers.base import ProviderRequest, ProviderResponse
from collections_copilot.llm.providers.fake import FakeLLM
from tests.factories import make_account
from tests.integration.graph_support import (
    FIXED_NOW,
    approve_latest,
    case_graph,
    config,
    make_services,
    new_case,
)

pytestmark = pytest.mark.integration

AP_NOTE = "AP contact confirmed the invoice was received and is in the approval queue."
PLAN_NOTE = "Owner asked to split the balance into installments; cash flow is tight this quarter."
# FIXED_NOW is 15:00 UTC = 00:00 in Tokyo: outside the contact window, so sends are deferred.
NEXT_MORNING = FIXED_NOW + timedelta(hours=8, minutes=30)


class Confident(FakeLLM):
    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        if request.route == "next_best_action":
            body = (
                '{"action": "remind", "rationale": "Routine reminder.", "confidence": 0.95,'
                ' "cited_note_ids": []}'
            )
            return ProviderResponse(body, "end_turn", "fake")
        return await super().complete(request)


async def _status(sm: Any, tenant: uuid.UUID, case_id: str) -> str:
    async with sm() as s:
        case = await Repos(s).cases.get(tenant, case_id)
        assert case is not None
        return case.status


async def _audit_actions(sm: Any, tenant: uuid.UUID) -> list[str]:
    async with sm() as s:
        rows = await s.execute(select(AuditEvent.action).where(AuditEvent.tenant_id == tenant))
        return list(rows.scalars())


async def _deferred_case(
    sm: Any, database_url: str, tenant: uuid.UUID, *, auto: bool = False, **account: Any
) -> tuple[str, uuid.UUID, InMemorySmsSender]:
    """A case approved (by a human, or auto) and paused at `deferred_send` until 08:00 Tokyo."""
    account_id = await make_account(
        sm,
        tenant,
        timezone="Asia/Tokyo",
        notes=[AP_NOTE if auto else PLAN_NOTE],
        invoices=[{"amount_cents": 30_000, "days_past_due": 20}],
        **account,
    )
    case_id = await new_case(sm, tenant, account_id)
    sms = InMemorySmsSender()
    llm = Confident() if auto else FakeLLM()
    async with case_graph(make_services(sm, database_url, llm, sms), database_url) as graph:
        await graph.ainvoke({"case_id": case_id, "tenant_id": str(tenant)}, config(case_id))
        if not auto:
            await approve_latest(sm, tenant, case_id)
            await graph.ainvoke(
                Command(resume={"kind": "approval", "decision": "approve", "actor": "ana"}),
                config(case_id),
            )
        snapshot = await graph.aget_state(config(case_id))
        assert snapshot.interrupts[0].value["kind"] == "deferred_send"
    assert sms.sent == []
    return case_id, account_id, sms


async def _resume_deferred(sm: Any, database_url: str, case_id: str, sms: Any) -> Any:
    services = make_services(sm, database_url, FakeLLM(), sms)
    services.clock = lambda: NEXT_MORNING
    async with case_graph(services, database_url) as graph:
        await graph.ainvoke(Command(resume={"kind": "deferred_send"}), config(case_id))
        return await graph.aget_state(config(case_id))


async def test_deferred_send_goes_out_when_nothing_changed(
    sessionmaker, database_url, tenant
) -> None:  # type: ignore[no-untyped-def]
    case_id, _, sms = await _deferred_case(sessionmaker, database_url, tenant)
    snapshot = await _resume_deferred(sessionmaker, database_url, case_id, sms)
    assert snapshot.next == ("await_reply",)
    assert len(sms.sent) == 1


async def test_dispute_after_approval_blocks_deferred_send(
    sessionmaker, database_url, tenant
) -> None:  # type: ignore[no-untyped-def]
    case_id, account_id, sms = await _deferred_case(sessionmaker, database_url, tenant)
    async with sessionmaker() as s, s.begin():
        await Repos(s).invoices.mark_disputed(tenant, account_id, "short shipment")
    await _resume_deferred(sessionmaker, database_url, case_id, sms)
    assert sms.sent == []
    assert "sms.blocked" in await _audit_actions(sessionmaker, tenant)


async def test_closed_case_blocks_deferred_send(sessionmaker, database_url, tenant) -> None:  # type: ignore[no-untyped-def]
    case_id, account_id, sms = await _deferred_case(sessionmaker, database_url, tenant)
    await cancel_open_work(sessionmaker, tenant, account_id, FIXED_NOW, "dispute")
    await _resume_deferred(sessionmaker, database_url, case_id, sms)
    assert sms.sent == []
    assert await _status(sessionmaker, tenant, case_id) == "closed"


async def test_cancelled_draft_blocks_deferred_send(sessionmaker, database_url, tenant) -> None:  # type: ignore[no-untyped-def]
    case_id, _, sms = await _deferred_case(sessionmaker, database_url, tenant)
    async with sessionmaker() as s, s.begin():
        await s.execute(
            update(Draft).where(Draft.case_id == uuid.UUID(case_id)).values(status="cancelled")
        )
    await _resume_deferred(sessionmaker, database_url, case_id, sms)
    assert sms.sent == []


async def test_opt_out_after_approval_blocks_deferred_send(
    sessionmaker, database_url, tenant
) -> None:  # type: ignore[no-untyped-def]
    case_id, account_id, sms = await _deferred_case(sessionmaker, database_url, tenant)
    async with sessionmaker() as s, s.begin():
        account = await Repos(s).accounts.get(tenant, account_id)
        assert account is not None
        await Repos(s).opt_outs.add(tenant, account.phone_e164, source="analyst")
    await _resume_deferred(sessionmaker, database_url, case_id, sms)
    assert sms.sent == []


async def test_wrong_number_flag_blocks_deferred_send(sessionmaker, database_url, tenant) -> None:  # type: ignore[no-untyped-def]
    case_id, account_id, sms = await _deferred_case(sessionmaker, database_url, tenant)
    async with sessionmaker() as s, s.begin():
        await Repos(s).accounts.set_contact_flag(tenant, account_id, "wrong_number")
    await _resume_deferred(sessionmaker, database_url, case_id, sms)
    assert sms.sent == []


async def test_missing_approval_record_blocks_send(sessionmaker, database_url, tenant) -> None:  # type: ignore[no-untyped-def]
    case_id, _, sms = await _deferred_case(sessionmaker, database_url, tenant)
    async with sessionmaker() as s, s.begin():
        await s.execute(delete(Approval).where(Approval.case_id == uuid.UUID(case_id)))
    await _resume_deferred(sessionmaker, database_url, case_id, sms)
    assert sms.sent == []


async def test_auto_approve_state_without_record_blocks_send(
    sessionmaker, database_url, tenant
) -> None:  # type: ignore[no-untyped-def]
    case_id, _, sms = await _deferred_case(sessionmaker, database_url, tenant, auto=True)
    async with sessionmaker() as s, s.begin():
        decisions = list(
            (
                await s.execute(
                    select(Approval.decision).where(Approval.case_id == uuid.UUID(case_id))
                )
            ).scalars()
        )
        assert decisions == ["auto_approve"]
        await s.execute(delete(Approval).where(Approval.case_id == uuid.UUID(case_id)))
    await _resume_deferred(sessionmaker, database_url, case_id, sms)
    assert sms.sent == []


async def test_send_uses_stored_draft_body(sessionmaker, database_url, tenant) -> None:  # type: ignore[no-untyped-def]
    case_id, _, sms = await _deferred_case(sessionmaker, database_url, tenant)
    async with case_graph(make_services(sessionmaker, database_url), database_url) as graph:
        snapshot = await graph.aget_state(config(case_id))
        tampered = {
            **snapshot.values["draft"],
            "body": "Pay USD 99,999.00 now. Reply STOP to opt out.",
        }
        await graph.aupdate_state(config(case_id), {"draft": tampered})
    await _resume_deferred(sessionmaker, database_url, case_id, sms)
    async with sessionmaker() as s:
        stored = await Repos(s).drafts.latest(tenant, uuid.UUID(case_id))
    assert stored is not None
    assert [m["body"] for m in sms.sent] == [stored.body]


async def test_dispute_before_approval_resume_returns_to_review(
    sessionmaker, database_url, tenant
) -> None:  # type: ignore[no-untyped-def]
    account_id = await make_account(sessionmaker, tenant, notes=[PLAN_NOTE])
    case_id = await new_case(sessionmaker, tenant, account_id)
    sms = InMemorySmsSender()
    async with case_graph(
        make_services(sessionmaker, database_url, sms=sms), database_url
    ) as graph:
        await graph.ainvoke({"case_id": case_id, "tenant_id": str(tenant)}, config(case_id))
        await approve_latest(sessionmaker, tenant, case_id)
        async with sessionmaker() as s, s.begin():
            await Repos(s).invoices.mark_disputed(tenant, account_id, "short shipment")
        await graph.ainvoke(
            Command(resume={"kind": "approval", "decision": "approve", "actor": "ana"}),
            config(case_id),
        )
        snapshot = await graph.aget_state(config(case_id))
    assert sms.sent == []
    assert snapshot.interrupts[0].value["kind"] == "approval"


async def test_approval_on_closed_case_closes_without_send(
    sessionmaker, database_url, tenant
) -> None:  # type: ignore[no-untyped-def]
    account_id = await make_account(sessionmaker, tenant, notes=[PLAN_NOTE])
    case_id = await new_case(sessionmaker, tenant, account_id)
    sms = InMemorySmsSender()
    async with case_graph(
        make_services(sessionmaker, database_url, sms=sms), database_url
    ) as graph:
        await graph.ainvoke({"case_id": case_id, "tenant_id": str(tenant)}, config(case_id))
        await approve_latest(sessionmaker, tenant, case_id)
        async with sessionmaker() as s:
            account = await Repos(s).accounts.get(tenant, account_id)
        assert account is not None
        await apply_stop(
            sessionmaker, sms, tenant_id=tenant, phone_e164=account.phone_e164, now=FIXED_NOW
        )
        confirmations = len(sms.sent)
        await graph.ainvoke(
            Command(resume={"kind": "approval", "decision": "approve", "actor": "ana"}),
            config(case_id),
        )
        snapshot = await graph.aget_state(config(case_id))
    assert len(sms.sent) == confirmations  # only the STOP confirmation, no reminder
    assert snapshot.next == ()
    assert await _status(sessionmaker, tenant, case_id) == "closed"


class CrashAfterDraftSaved(DbCaseStore):
    """The worker dies after the draft row is written but before the node is checkpointed."""

    async def save_draft(self, state: Any, **fields: Any) -> None:
        await super().save_draft(state, **fields)
        if fields.get("postcheck") is None:
            raise RuntimeError("worker killed after save_draft")


class OtherWording(FakeLLM):
    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        if request.route == "draft_message":
            raise AssertionError("the stored draft must be reused, not regenerated")
        return await super().complete(request)


async def test_redraft_after_crash_reuses_stored_row(sessionmaker, database_url, tenant) -> None:  # type: ignore[no-untyped-def]
    account_id = await make_account(sessionmaker, tenant, notes=[PLAN_NOTE])
    case_id = await new_case(sessionmaker, tenant, account_id)
    services = make_services(sessionmaker, database_url)
    services.store = CrashAfterDraftSaved(sessionmaker)
    async with case_graph(services, database_url) as graph:
        with pytest.raises(RuntimeError, match="worker killed"):
            await graph.ainvoke({"case_id": case_id, "tenant_id": str(tenant)}, config(case_id))
    async with case_graph(
        make_services(sessionmaker, database_url, OtherWording()), database_url
    ) as graph:
        await graph.ainvoke(None, config(case_id))
        snapshot = await graph.aget_state(config(case_id))
    async with sessionmaker() as s:
        [stored] = await Repos(s).drafts.for_case(tenant, uuid.UUID(case_id))
    assert snapshot.values["draft"]["body"] == stored.body


async def test_add_contact_is_idempotent_per_message(sessionmaker, database_url, tenant) -> None:  # type: ignore[no-untyped-def]
    account_id = await make_account(sessionmaker, tenant)
    case_id = await new_case(sessionmaker, tenant, account_id)
    store = DbCaseStore(sessionmaker)
    state = {"tenant_id": str(tenant), "case_id": case_id, "facts": {"account_id": str(account_id)}}
    claim = await store.claim_message(state, f"sms:{case_id}:1", "+15550000001", "hi", 1)
    await store.add_contact(state, claim["id"], FIXED_NOW)
    await store.add_contact(state, claim["id"], FIXED_NOW)
    async with sessionmaker() as s:
        rows = (
            await s.execute(select(ContactLog).where(ContactLog.account_id == account_id))
        ).all()
    assert len(rows) == 1

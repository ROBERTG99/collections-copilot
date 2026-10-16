"""ORM row → API model conversions shared by the routers."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, cast

from collections_copilot.api.schemas import (
    AgingBuckets,
    ApprovalOut,
    CaseSummary,
    ComplianceOut,
    DraftOut,
    InvoiceOut,
    MessageOut,
    NoteOut,
    PromiseOut,
    StepOut,
)
from collections_copilot.compliance import engine
from collections_copilot.db.models import (
    Account,
    Approval,
    Case,
    Draft,
    Invoice,
    Message,
    Note,
    Promise,
    StepRecordRow,
)
from collections_copilot.db.repositories import Repos


def days_past_due(inv: Invoice, today: date) -> int:
    return max(0, (today - inv.due_on).days)


def invoice_out(inv: Invoice, today: date) -> InvoiceOut:
    return InvoiceOut(
        number=inv.number,
        amount_cents=inv.amount_cents,
        currency=inv.currency,
        issued_on=inv.issued_on,
        due_on=inv.due_on,
        days_past_due=days_past_due(inv, today),
        status=inv.status,
        dispute_reason=inv.dispute_reason,
    )


def aging(invoices: list[Invoice], today: date) -> AgingBuckets:
    out = AgingBuckets()
    for inv in invoices:
        if inv.status != "open":
            continue
        dpd = days_past_due(inv, today)
        if dpd == 0:
            out.current += inv.amount_cents
        elif dpd <= 30:
            out.d1_30 += inv.amount_cents
        elif dpd <= 60:
            out.d31_60 += inv.amount_cents
        elif dpd <= 90:
            out.d61_90 += inv.amount_cents
        else:
            out.d90_plus += inv.amount_cents
    return out


def note_out(n: Note) -> NoteOut:
    return NoteOut(id=n.id, source=n.source, body=n.body, created_at=n.created_at)


def case_summary(c: Case) -> CaseSummary:
    return CaseSummary(
        id=c.id,
        status=c.status,
        priority_score=c.priority_score,
        nba_action=c.nba_action,
        nba_confidence=c.nba_confidence,
        nba_rationale=c.nba_rationale,
        cited_note_ids=list(c.cited_note_ids or []),
        fallback=c.fallback,
        requires_edit=c.requires_edit,
        analyst_reason=c.analyst_reason,
        created_at=c.created_at,
        closed_at=c.closed_at,
    )


def message_out(m: Message) -> MessageOut:
    return MessageOut(
        id=m.id,
        direction=cast(Any, m.direction),
        body=m.body,
        status=m.status,
        classification=m.classification,
        classification_confidence=m.classification_confidence,
        extracted=m.extracted,
        case_id=m.case_id,
        sent_at=m.sent_at,
        created_at=m.created_at,
    )


def promise_out(p: Promise) -> PromiseOut:
    return PromiseOut(
        id=p.id,
        account_id=p.account_id,
        case_id=p.case_id,
        amount_cents=p.amount_cents,
        currency=p.currency,
        promised_date=p.promised_date,
        invoice_numbers=list(p.invoice_numbers),
        status=p.status,
        mcp_approval_id=p.mcp_approval_id,
        ledger_ref=p.ledger_ref,
        failure_reason=p.failure_reason,
        created_at=p.created_at,
    )


def draft_out(d: Draft) -> DraftOut:
    return DraftOut(
        version=d.version,
        body=d.body,
        language=d.language,
        author=cast(Any, d.author),
        model=d.model,
        prompt_version=d.prompt_version,
        status=d.status,
        postcheck=d.postcheck,
        created_at=d.created_at,
    )


def approval_out(a: Approval, versions: dict[Any, int]) -> ApprovalOut:
    return ApprovalOut(
        draft_version=versions.get(a.draft_id, 0),
        decision=a.decision,
        reason=a.reason,
        diff=a.diff,
        decided_by=a.decided_by,
        send_at=a.send_at,
        decided_at=a.decided_at,
    )


def step_out(s: StepRecordRow) -> StepOut:
    return StepOut(
        node=s.node,
        kind=s.kind,
        model=s.model,
        prompt_version=s.prompt_version,
        input_tokens=s.input_tokens,
        output_tokens=s.output_tokens,
        cost_usd=float(s.cost_usd),
        latency_ms=s.latency_ms,
        status=s.status,
        started_at=s.started_at,
    )


async def live_precheck(
    repos: Repos, account: Account, invoices: list[Invoice], now: datetime
) -> ComplianceOut:
    """The compliance pre-check as of now (opt-out, 7-day cap, disputes, contact window)."""
    tenant = account.tenant_id
    opted = account.opted_out_at is not None or await repos.opt_outs.is_opted_out(
        tenant, account.phone_e164
    )
    ctx = engine.ComplianceContext(
        now=now,
        account=engine.AccountFacts(account.timezone, account.language, account.currency, opted),
        invoices=[
            engine.InvoiceFacts(i.number, i.amount_cents, i.currency, i.status) for i in invoices
        ],
        contacts_7d=await repos.contacts.recent(tenant, account.id, now),
        account_name=account.name,
        contact_flag=account.contact_flag,
    )
    result = engine.precheck(ctx)
    return ComplianceOut(
        ok=result.ok,
        violations=[v.as_dict() for v in result.violations],
        send_at=result.send_at,
        deferred=result.deferred,
    )

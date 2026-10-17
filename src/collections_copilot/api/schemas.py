"""API response and request models (OpenAPI → web/lib/api/schema.d.ts via `make gen-types`)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class Money(BaseModel):
    amount_cents: int
    currency: str


class QueueItem(BaseModel):
    account_id: uuid.UUID
    external_ref: str
    name: str
    segment: str
    language: str
    timezone: str = "UTC"  # the debtor's IANA zone: contact windows and send times use it
    priority_score: int
    score_components: dict[str, float] | None
    open_balance: Money
    max_days_past_due: int
    opted_out: bool
    contact_flag: str | None
    case_id: uuid.UUID | None
    case_status: str | None
    nba_action: str | None
    nba_confidence: float | None
    fallback: bool
    compliance_ok: bool | None
    compliance_codes: list[str]
    draft_version: int | None = None  # the latest draft while the case awaits approval
    draft_body: str | None = None


class QueuePage(BaseModel):
    items: list[QueueItem]
    next_cursor: str | None


class InvoiceOut(BaseModel):
    number: str
    amount_cents: int
    currency: str
    issued_on: date
    due_on: date
    days_past_due: int
    status: str
    dispute_reason: str | None


class NoteOut(BaseModel):
    id: uuid.UUID
    source: str
    body: str
    created_at: datetime


class CaseSummary(BaseModel):
    id: uuid.UUID
    status: str
    priority_score: int | None
    nba_action: str | None
    nba_confidence: float | None
    nba_rationale: str | None
    cited_note_ids: list[uuid.UUID]
    fallback: bool
    requires_edit: bool
    analyst_reason: str | None
    created_at: datetime
    closed_at: datetime | None


class MessageOut(BaseModel):
    id: uuid.UUID
    direction: Literal["outbound", "inbound"]
    body: str
    status: str
    classification: str | None
    classification_confidence: float | None
    extracted: dict[str, Any] | None
    case_id: uuid.UUID | None
    sent_at: datetime | None
    created_at: datetime
    classification_source: Literal["rule", "model", "fallback", "unknown"] | None = None
    corrected_label: str | None = None  # an analyst's correction; never read by the graph
    corrected_by: str | None = None
    corrected_at: datetime | None = None
    account_id: uuid.UUID | None = None
    account_name: str | None = None
    account_ref: str | None = None
    currency: str | None = None


class PromiseOut(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    case_id: uuid.UUID | None
    amount_cents: int
    currency: str
    promised_date: date
    invoice_numbers: list[str]
    status: str
    mcp_approval_id: str | None
    ledger_ref: str | None
    failure_reason: str | None
    created_at: datetime
    account_name: str | None = None
    account_ref: str | None = None


class AgingBuckets(BaseModel):
    current: int = 0
    d1_30: int = 0
    d31_60: int = 0
    d61_90: int = 0
    d90_plus: int = 0


class ComplianceOut(BaseModel):
    ok: bool
    violations: list[dict[str, Any]]
    send_at: datetime
    deferred: bool


class AccountDetail(BaseModel):
    id: uuid.UUID
    external_ref: str
    name: str
    phone_e164: str
    timezone: str
    segment: str
    language: str
    currency: str
    opted_out: bool
    contact_flag: str | None
    priority_score: int
    score_components: dict[str, float] | None
    open_balance: Money
    aging: AgingBuckets
    invoices: list[InvoiceOut]
    notes: list[NoteOut]
    cases: list[CaseSummary]
    messages: list[MessageOut]
    promises: list[PromiseOut]
    precheck: ComplianceOut


class CreateCases(BaseModel):
    account_ids: list[uuid.UUID] = Field(min_length=1, max_length=400)


class CreatedCase(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    status: str
    existing: bool


class CreateCasesOut(BaseModel):
    cases: list[CreatedCase]


class DraftOut(BaseModel):
    version: int
    body: str
    language: str
    author: Literal["ai", "analyst", "fallback"]
    model: str | None
    prompt_version: str | None
    status: str
    postcheck: dict[str, Any] | None
    created_at: datetime


class ApprovalOut(BaseModel):
    draft_version: int
    decision: str
    reason: str | None
    diff: str | None
    decided_by: str
    send_at: datetime | None
    decided_at: datetime


class StepOut(BaseModel):
    node: str
    kind: str
    model: str | None
    prompt_version: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float
    status: str
    started_at: datetime


class CaseDetail(BaseModel):
    case: CaseSummary
    account: QueueItem
    drafts: list[DraftOut]
    approvals: list[ApprovalOut]
    steps: list[StepOut]
    cited_notes: list[NoteOut]
    messages: list[MessageOut]
    precheck: dict[str, Any] | None
    recipient_e164: str  # exactly who an approved draft is sent to


class ViolationOut(BaseModel):
    code: str
    message: str
    blocking: bool = True


class CheckOut(BaseModel):
    """A compliance pre-check or post-check result."""

    ok: bool
    violations: list[ViolationOut]
    send_at: datetime
    deferred: bool

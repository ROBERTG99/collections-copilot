"""SQLAlchemy 2 models (BUILD.md §4.3). Every table carries tenant_id; money is bigint cents."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        dict[str, Any]: JSONB,
        uuid.UUID: UUID(as_uuid=True),
    }


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _tenant() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


SEGMENTS = ("enterprise", "mid_market", "smb")
LANGUAGES = ("en", "es")
CASE_STATUSES = (
    "queued",
    "processing",
    "awaiting_approval",
    "scheduled",
    "sent",
    "awaiting_reply",
    "requires_analyst",
    "escalated",
    "closed",
    "failed",
)
OPEN_CASE_PREDICATE = "status NOT IN ('closed', 'failed')"
NBA_ACTIONS = ("remind", "payment_plan", "escalate", "pause", "no_action")


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    created_at: Mapped[datetime] = _created()


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_ref", name="uq_accounts_tenant_ref"),
        Index("ix_accounts_queue", "tenant_id", text("priority_score DESC"), "id"),
        CheckConstraint(f"segment IN {SEGMENTS}", name="ck_accounts_segment"),
        CheckConstraint(f"language IN {LANGUAGES}", name="ck_accounts_language"),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    external_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone_e164: Mapped[str] = mapped_column(String(20), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    segment: Mapped[str] = mapped_column(String(20), nullable=False)
    language: Mapped[str] = mapped_column(String(2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")
    opted_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    contact_flag: Mapped[str | None] = mapped_column(String(32))
    # Cached deterministic priority (scoring/priority.py): refreshed on ingest, seed, broken promises
    # and daily by the scheduler (docs/DECISIONS.md).
    priority_score: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    score_components: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    open_balance_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    max_days_past_due: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    score_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "number", name="uq_invoices_tenant_number"),
        CheckConstraint("amount_cents > 0", name="ck_invoices_amount_positive"),
        CheckConstraint(
            "status IN ('open', 'paid', 'disputed', 'written_off')", name="ck_invoices_status"
        ),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id"), nullable=False, index=True
    )
    number: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    issued_on: Mapped[date] = mapped_column(Date, nullable=False)
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="open")
    dispute_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class Note(Base):
    __tablename__ = "notes"
    __table_args__ = (Index("ix_notes_account_created", "account_id", "created_at"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created()


class IngestBatch(Base):
    __tablename__ = "ingest_batches"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sha256", name="uq_ingest_tenant_sha"),
        CheckConstraint(
            "status IN ('awaiting_upload', 'completed', 'rejected')", name="ck_ingest_status"
        ),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    sha256: Mapped[str | None] = mapped_column(String(64))
    object_key: Mapped[str | None] = mapped_column(String(512))
    filename: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    errors: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = _created()


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        Index(
            "uq_cases_one_open_per_account",
            "tenant_id",
            "account_id",
            unique=True,
            postgresql_where=text(OPEN_CASE_PREDICATE),
        ),
        Index("ix_cases_tenant_status_priority", "tenant_id", "status", "priority_score"),
        CheckConstraint(f"status IN {CASE_STATUSES}", name="ck_cases_status"),
        CheckConstraint(
            f"nba_action IS NULL OR nba_action IN {NBA_ACTIONS}", name="ck_cases_nba_action"
        ),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    priority_score: Mapped[int | None] = mapped_column(Integer)
    score_components: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    priority_rationale: Mapped[str | None] = mapped_column(Text)
    nba_action: Mapped[str | None] = mapped_column(String(20))
    nba_confidence: Mapped[float | None] = mapped_column(Float)
    nba_rationale: Mapped[str | None] = mapped_column(Text)
    cited_note_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    requires_edit: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    analyst_reason: Mapped[str | None] = mapped_column(String(64))
    precheck: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Draft(Base):
    __tablename__ = "drafts"
    __table_args__ = (
        UniqueConstraint("case_id", "version", name="uq_drafts_case_version"),
        CheckConstraint("author IN ('ai', 'analyst', 'fallback')", name="ck_drafts_author"),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(2), nullable=False)
    author: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(64))
    precheck: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    postcheck: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    created_at: Mapped[datetime] = _created()


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('approve', 'edit', 'reject', 'auto_approve')",
            name="ck_approvals_decision",
        ),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    draft_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("drafts.id"), nullable=False, unique=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    diff: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    send_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime] = _created()


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("direction IN ('outbound', 'inbound')", name="ck_messages_direction"),
        Index("ix_messages_account_created", "account_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id"))
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id"), index=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    provider_sid: Mapped[str | None] = mapped_column(String(64), unique=True)
    to_e164: Mapped[str | None] = mapped_column(String(20))
    from_e164: Mapped[str | None] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    classification: Mapped[str | None] = mapped_column(String(20))
    classification_confidence: Mapped[float | None] = mapped_column(Float)
    extracted: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    draft_version: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class MessageStatusEvent(Base):
    __tablename__ = "message_status_events"
    __table_args__ = (UniqueConstraint("provider_sid", "status", name="uq_status_sid_status"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    provider_sid: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created()


class Promise(Base):
    __tablename__ = "promises"
    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="ck_promises_amount_positive"),
        CheckConstraint(
            "status IN ('pending', 'approval_required', 'recorded', 'failed', 'kept', 'broken',"
            " 'rejected')",
            name="ck_promises_status",
        ),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id"), nullable=False, index=True
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id"))
    message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("messages.id"))
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    promised_date: Mapped[date] = mapped_column(Date, nullable=False)
    invoice_numbers: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    mcp_approval_id: Mapped[str | None] = mapped_column(String(64))
    ledger_ref: Mapped[str | None] = mapped_column(String(64))
    failure_reason: Mapped[str | None] = mapped_column(String(64))
    decided_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class FollowUp(Base):
    __tablename__ = "follow_ups"
    __table_args__ = (
        Index("ix_follow_ups_status_due", "status", "due_at"),
        CheckConstraint(
            "kind IN ('after_send', 'deferred_send', 'promise_due', 'approval_reminder',"
            " 'payment_verification', 'send_retry')",
            name="ck_follow_ups_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'claimed', 'done', 'cancelled')", name="ck_follow_ups_status"
        ),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id"))
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    promise_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("promises.id"))
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="pending")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = _created()


class OptOut(Base):
    __tablename__ = "opt_outs"
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )
    phone_e164: Mapped[str] = mapped_column(String(20), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = _created()


class ContactLog(Base):
    __tablename__ = "contact_log"
    __table_args__ = (Index("ix_contact_log_cap", "tenant_id", "account_id", "sent_at"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("messages.id"))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Setting(Base):
    __tablename__ = "settings"
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StepRecordRow(Base):
    __tablename__ = "step_records"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = _tenant()
    case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    node: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    prompt_hash: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, server_default="0")
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_tenant_seq", "tenant_id", "seq"),)
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    ts: Mapped[datetime] = _created()
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64))
    payload_redacted: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)


class ApiIdempotency(Base):
    __tablename__ = "api_idempotency"
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created()

"""Case data access for the graph: Postgres in the worker, in-memory for evals.

Every method is tenant-scoped through the graph state and idempotent, because LangGraph re-runs
a node from its start after a crash or an interrupt resume.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collections_copilot.db import audit as audit_log
from collections_copilot.db.models import Draft as DraftRow
from collections_copilot.db.repositories import Repos

DEFAULT_POLICY = {"enabled": True, "max_usd": 500.0, "min_confidence": 0.9}


@dataclass(frozen=True)
class InvoiceRow:
    number: str
    amount_cents: int
    currency: str
    status: str
    days_past_due: int


@dataclass(frozen=True)
class NoteFact:
    id: str
    source: str
    body: str
    created_at: str = ""


@dataclass(frozen=True)
class CaseFacts:
    tenant_id: str
    case_id: str
    account_id: str
    external_ref: str
    name: str
    phone_e164: str
    timezone: str
    segment: str
    language: str
    currency: str
    opted_out: bool
    contact_flag: str | None
    invoices: list[InvoiceRow]
    notes: list[NoteFact]
    broken_promises_90d: int
    contacts_7d: list[str]
    auto_policy: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_POLICY))

    def as_state(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_state(data: dict[str, Any]) -> CaseFacts:
        return CaseFacts(
            **{
                **data,
                "invoices": [InvoiceRow(**i) for i in data["invoices"]],
                "notes": [NoteFact(**n) for n in data["notes"]],
            }
        )


class CaseStore(Protocol):
    async def load(self, state: Mapping[str, Any], now: datetime) -> CaseFacts: ...

    async def recheck(
        self, state: Mapping[str, Any], now: datetime
    ) -> tuple[bool, list[datetime]]: ...

    async def set_case(self, state: Mapping[str, Any], **fields: Any) -> None: ...

    async def save_draft(self, state: Mapping[str, Any], **fields: Any) -> None: ...

    async def approval_for(
        self, state: Mapping[str, Any], version: int
    ) -> dict[str, Any] | None: ...

    async def latest_draft(self, state: Mapping[str, Any]) -> dict[str, Any] | None: ...

    async def record_auto_approval(
        self, state: Mapping[str, Any], version: int, policy: dict[str, Any], send_at: datetime
    ) -> None: ...

    async def claim_message(
        self, state: Mapping[str, Any], key: str, to_e164: str, body: str, version: int
    ) -> dict[str, Any]: ...

    async def mark_message(
        self, state: Mapping[str, Any], message_id: str, **fields: Any
    ) -> None: ...

    async def add_contact(
        self, state: Mapping[str, Any], message_id: str, sent_at: datetime
    ) -> None: ...

    async def follow_up(
        self,
        state: Mapping[str, Any],
        kind: str,
        due_at: datetime,
        payload: dict[str, Any] | None = None,
    ) -> None: ...

    async def audit(
        self,
        state: Mapping[str, Any],
        action: str,
        entity: str,
        entity_id: str | None,
        payload: dict[str, Any],
        actor_type: str = "system",
        actor_id: str = "cc-worker",
    ) -> None: ...


def _uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class DbCaseStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self.sm = sessionmaker

    async def load(self, state: Mapping[str, Any], now: datetime) -> CaseFacts:
        tenant, case_id = _uuid(state["tenant_id"]), _uuid(state["case_id"])
        async with self.sm() as s:
            repos = Repos(s)
            case = await repos.cases.get(tenant, case_id)
            if case is None:
                raise LookupError(f"case {case_id} not found for tenant")
            account = await repos.accounts.get(tenant, case.account_id)
            assert account is not None
            today = now.date()
            invoices = [
                InvoiceRow(
                    i.number, i.amount_cents, i.currency, i.status, max(0, (today - i.due_on).days)
                )
                for i in await repos.invoices.for_account(tenant, account.id)
            ]
            notes = [
                NoteFact(str(n.id), n.source, n.body, n.created_at.isoformat())
                for n in reversed(await repos.notes.for_account(tenant, account.id, limit=20))
            ]
            broken = await repos.promises.broken_since(
                tenant, account.id, today - timedelta(days=90)
            )
            contacts = await repos.contacts.recent(tenant, account.id, now)
            opted = account.opted_out_at is not None or await repos.opt_outs.is_opted_out(
                tenant, account.phone_e164
            )
            policy = await repos.settings.get(tenant, "auto_approval") or {}
            return CaseFacts(
                tenant_id=str(tenant),
                case_id=str(case_id),
                account_id=str(account.id),
                external_ref=account.external_ref,
                name=account.name,
                phone_e164=account.phone_e164,
                timezone=account.timezone,
                segment=account.segment,
                language=account.language,
                currency=account.currency,
                opted_out=opted,
                contact_flag=account.contact_flag,
                invoices=invoices,
                notes=notes,
                broken_promises_90d=broken,
                contacts_7d=[c.isoformat() for c in contacts],
                auto_policy={**DEFAULT_POLICY, **policy},
            )

    async def recheck(self, state: Mapping[str, Any], now: datetime) -> tuple[bool, list[datetime]]:
        tenant = _uuid(state["tenant_id"])
        facts = state["facts"]
        async with self.sm() as s:
            repos = Repos(s)
            account = await repos.accounts.get(tenant, facts["account_id"])
            opted = account is None or account.opted_out_at is not None
            opted = opted or await repos.opt_outs.is_opted_out(tenant, facts["phone_e164"])
            contacts = await repos.contacts.recent(tenant, _uuid(facts["account_id"]), now)
            return opted, contacts

    async def set_case(self, state: Mapping[str, Any], **fields: Any) -> None:
        async with self.sm() as s, s.begin():
            await Repos(s).cases.set(_uuid(state["tenant_id"]), _uuid(state["case_id"]), **fields)

    async def save_draft(self, state: Mapping[str, Any], **fields: Any) -> None:
        tenant, case_id = _uuid(state["tenant_id"]), _uuid(state["case_id"])
        async with self.sm() as s, s.begin():
            stmt = insert(DraftRow).values(
                id=uuid.uuid4(), tenant_id=tenant, case_id=case_id, **fields
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["case_id", "version"],
                set_={"postcheck": stmt.excluded.postcheck, "precheck": stmt.excluded.precheck},
            )
            await s.execute(stmt)

    async def latest_draft(self, state: Mapping[str, Any]) -> dict[str, Any] | None:
        async with self.sm() as s:
            d = await Repos(s).drafts.latest(_uuid(state["tenant_id"]), _uuid(state["case_id"]))
            if d is None:
                return None
            return {
                "version": d.version,
                "body": d.body,
                "author": d.author,
                "postcheck": d.postcheck,
            }

    async def approval_for(self, state: Mapping[str, Any], version: int) -> dict[str, Any] | None:
        tenant, case_id = _uuid(state["tenant_id"]), _uuid(state["case_id"])
        async with self.sm() as s:
            repos = Repos(s)
            draft = await repos.drafts.get_version(tenant, case_id, version)
            if draft is None:
                return None
            approval = await repos.approvals.for_draft(tenant, draft.id)
            if approval is None:
                return None
            return {
                "decision": approval.decision,
                "decided_by": approval.decided_by,
                "send_at": approval.send_at.isoformat() if approval.send_at else None,
            }

    async def record_auto_approval(
        self, state: Mapping[str, Any], version: int, policy: dict[str, Any], send_at: datetime
    ) -> None:
        tenant, case_id = _uuid(state["tenant_id"]), _uuid(state["case_id"])
        async with self.sm() as s, s.begin():
            repos = Repos(s)
            draft = await repos.drafts.get_version(tenant, case_id, version)
            assert draft is not None
            if await repos.approvals.for_draft(tenant, draft.id) is not None:
                return
            await repos.approvals.add(
                tenant,
                draft_id=draft.id,
                case_id=case_id,
                decision="auto_approve",
                decided_by="system:auto-approval",
                policy_snapshot=policy,
                send_at=send_at,
            )
            draft.status = "approved"
            await audit_log.append(
                s,
                tenant,
                actor_type="system",
                actor_id="auto-approval",
                action="approval.auto_approve",
                entity="case",
                entity_id=str(case_id),
                payload={"draft_version": version, "policy": policy},
            )

    async def claim_message(
        self, state: Mapping[str, Any], key: str, to_e164: str, body: str, version: int
    ) -> dict[str, Any]:
        tenant = _uuid(state["tenant_id"])
        async with self.sm() as s, s.begin():
            row, created = await Repos(s).messages.claim(
                tenant,
                key,
                account_id=_uuid(state["facts"]["account_id"]),
                case_id=_uuid(state["case_id"]),
                direction="outbound",
                to_e164=to_e164,
                body=body,
                status="pending",
                draft_version=version,
            )
            return {
                "id": str(row.id),
                "created": created,
                "status": row.status,
                "provider_sid": row.provider_sid,
                "attempts": row.attempts,
            }

    async def mark_message(self, state: Mapping[str, Any], message_id: str, **fields: Any) -> None:
        async with self.sm() as s, s.begin():
            await Repos(s).messages.set(_uuid(state["tenant_id"]), _uuid(message_id), **fields)

    async def add_contact(
        self, state: Mapping[str, Any], message_id: str, sent_at: datetime
    ) -> None:
        async with self.sm() as s, s.begin():
            await Repos(s).contacts.add(
                _uuid(state["tenant_id"]),
                _uuid(state["facts"]["account_id"]),
                _uuid(message_id),
                sent_at,
            )

    async def follow_up(
        self,
        state: Mapping[str, Any],
        kind: str,
        due_at: datetime,
        payload: dict[str, Any] | None = None,
    ) -> None:
        tenant, case_id = _uuid(state["tenant_id"]), _uuid(state["case_id"])
        async with self.sm() as s, s.begin():
            repos = Repos(s)
            if await repos.follow_ups.pending_for_case(tenant, case_id, kind) is not None:
                return
            await repos.follow_ups.add(
                tenant,
                case_id=case_id,
                account_id=_uuid(state["facts"]["account_id"]),
                kind=kind,
                due_at=due_at,
                payload=payload or {},
            )

    async def audit(
        self,
        state: Mapping[str, Any],
        action: str,
        entity: str,
        entity_id: str | None,
        payload: dict[str, Any],
        actor_type: str = "system",
        actor_id: str = "cc-worker",
    ) -> None:
        async with self.sm() as s, s.begin():
            await audit_log.append(
                s,
                _uuid(state["tenant_id"]),
                actor_type=actor_type,
                actor_id=actor_id,
                action=action,
                entity=entity,
                entity_id=entity_id,
                payload=payload,
            )


class MemoryCaseStore:
    """Eval store: facts come from the graph input (`input_facts`); writes are recorded only."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self.drafts: dict[tuple[str, int], dict[str, Any]] = {}

    async def load(self, state: Mapping[str, Any], now: datetime) -> CaseFacts:
        return CaseFacts.from_state(state["input_facts"])

    async def recheck(self, state: Mapping[str, Any], now: datetime) -> tuple[bool, list[datetime]]:
        facts = state["facts"]
        return bool(facts["opted_out"]), [datetime.fromisoformat(c) for c in facts["contacts_7d"]]

    async def set_case(self, state: Mapping[str, Any], **fields: Any) -> None:
        self.writes.append(("case", fields))

    async def save_draft(self, state: Mapping[str, Any], **fields: Any) -> None:
        self.drafts[(state["case_id"], int(fields["version"]))] = fields

    async def latest_draft(self, state: Mapping[str, Any]) -> dict[str, Any] | None:
        versions = [v for (c, v) in self.drafts if c == state["case_id"]]
        return self.drafts[(state["case_id"], max(versions))] if versions else None

    async def approval_for(self, state: Mapping[str, Any], version: int) -> dict[str, Any] | None:
        return None

    async def record_auto_approval(
        self, state: Mapping[str, Any], version: int, policy: dict[str, Any], send_at: datetime
    ) -> None:
        self.writes.append(("auto_approval", {"version": version}))

    async def claim_message(
        self, state: Mapping[str, Any], key: str, to_e164: str, body: str, version: int
    ) -> dict[str, Any]:
        return {
            "id": str(uuid.uuid4()),
            "created": True,
            "status": "pending",
            "provider_sid": None,
            "attempts": 0,
        }

    async def mark_message(self, state: Mapping[str, Any], message_id: str, **fields: Any) -> None:
        self.writes.append(("message", fields))

    async def add_contact(
        self, state: Mapping[str, Any], message_id: str, sent_at: datetime
    ) -> None:
        self.writes.append(("contact", {"message_id": message_id}))

    async def follow_up(
        self,
        state: Mapping[str, Any],
        kind: str,
        due_at: datetime,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.writes.append(("follow_up", {"kind": kind, "due_at": due_at.isoformat()}))

    async def audit(
        self,
        state: Mapping[str, Any],
        action: str,
        entity: str,
        entity_id: str | None,
        payload: dict[str, Any],
        actor_type: str = "system",
        actor_id: str = "cc-worker",
    ) -> None:
        self.writes.append(("audit", {"action": action}))


def utcnow() -> datetime:
    return datetime.now(UTC)


def business_days_after(start: datetime, days: int) -> datetime:
    current = start
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def iso_date(value: date) -> str:
    return value.isoformat()

"""Load gen.v1 tenants into the application database (idempotent by digest)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collections_copilot.db import audit
from collections_copilot.db.models import Note, Promise
from collections_copilot.db.repositories import Repos
from data.generator.gen import GEN_VERSION, TenantSpec

SEED_SETTING = "seed"


async def load_tenant(
    sm: async_sessionmaker[AsyncSession], spec: TenantSpec, as_of: date | None = None
) -> bool:
    """Load one tenant. Returns False when the same digest is already loaded."""
    as_of = as_of or datetime.now(UTC).date()
    digest = spec.digest()
    async with sm() as session, session.begin():
        repos = Repos(session)
        await repos.tenants.ensure(spec.tenant_id, spec.name)
        current = await repos.settings.get(spec.tenant_id, SEED_SETTING)
        if current and current.get("digest") == digest:
            await repos.queue.refresh_scores(spec.tenant_id, datetime.now(UTC).date())
            return False
        now = datetime.now(UTC)
        for acc in spec.accounts:
            account = await repos.accounts.upsert(
                spec.tenant_id,
                {
                    "external_ref": acc.external_ref,
                    "name": acc.name,
                    "phone_e164": acc.phone_e164,
                    "timezone": acc.timezone,
                    "segment": acc.segment,
                    "language": acc.language,
                    "currency": acc.currency,
                },
            )
            for inv in acc.invoices:
                due = as_of - timedelta(days=inv.days_past_due)
                await repos.invoices.upsert(
                    spec.tenant_id,
                    account.id,
                    {
                        "number": inv.number,
                        "amount_cents": inv.amount_cents,
                        "currency": acc.currency,
                        "issued_on": due - timedelta(days=30),
                        "due_on": due,
                        "status": inv.status,
                        "dispute_reason": inv.dispute_reason,
                    },
                )
            for note in acc.notes:
                session.add(
                    Note(
                        tenant_id=spec.tenant_id,
                        account_id=account.id,
                        source=note.source,
                        body=note.body,
                        created_at=now - timedelta(days=note.days_ago),
                    )
                )
            for n, p in enumerate(acc.promises):
                session.add(
                    Promise(
                        tenant_id=spec.tenant_id,
                        account_id=account.id,
                        amount_cents=p.amount_cents,
                        currency=acc.currency,
                        promised_date=as_of - timedelta(days=p.due_days_ago),
                        invoice_numbers=[acc.invoices[0].number],
                        status=p.status,
                        idempotency_key=f"seed:{spec.tenant_id}:{acc.external_ref}:{n}",
                    )
                )
        await session.flush()
        await repos.queue.refresh_scores(spec.tenant_id, as_of)
        await repos.settings.put(
            spec.tenant_id,
            SEED_SETTING,
            {
                "version": GEN_VERSION,
                "seed": spec.seed,
                "digest": digest,
                "accounts": len(spec.accounts),
                "as_of": as_of.isoformat(),
            },
            updated_by="seed",
        )
        await audit.append(
            session,
            spec.tenant_id,
            actor_type="system",
            actor_id="seed",
            action="seed.load",
            entity="tenant",
            entity_id=str(spec.tenant_id),
            payload={"version": GEN_VERSION, "digest": digest, "accounts": len(spec.accounts)},
        )
    return True


async def load_ledger(libpq_url: str, spec: TenantSpec, as_of: date | None = None) -> None:
    """Mirror accounts and invoices into the synthetic ledger (schema `ledger`)."""
    from ledger_mcp.store import LedgerStore

    as_of = as_of or datetime.now(UTC).date()
    store = LedgerStore(libpq_url)
    await store.open()
    try:
        tenant = str(spec.tenant_id)
        for acc in spec.accounts:
            await store.upsert_account(tenant, acc.external_ref, acc.name, acc.currency)
            for inv in acc.invoices:
                await store.upsert_invoice(
                    tenant,
                    acc.external_ref,
                    inv.number,
                    inv.amount_cents,
                    acc.currency,
                    as_of - timedelta(days=inv.days_past_due),
                    inv.status,
                )
    finally:
        await store.close()

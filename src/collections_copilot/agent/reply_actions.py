"""Side effects shared by the inbound webhook and the reply graph (tenant-scoped, idempotent)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collections_copilot.agent.prompts import template
from collections_copilot.db import audit
from collections_copilot.db.models import Case
from collections_copilot.db.repositories import Repos
from collections_copilot.db.repositories.cases import OPEN_STATUSES
from collections_copilot.integrations.sms.base import SmsError, SmsSender

log = logging.getLogger(__name__)


async def apply_stop(
    sm: async_sessionmaker[AsyncSession],
    sms: SmsSender,
    *,
    tenant_id: uuid.UUID,
    phone_e164: str,
    now: datetime,
    source: str = "sms_stop",
) -> bool:
    """Opt out, cancel pending drafts, close open cases, send ONE confirmation, audit.
    Returns True if this call created the opt-out (later STOPs are no-ops)."""
    async with sm() as s, s.begin():
        repos = Repos(s)
        created = await repos.opt_outs.add(tenant_id, phone_e164, source=source)
        await repos.accounts.set_opted_out(tenant_id, phone_e164, now)
        accounts = await repos.accounts.get_by_phone(tenant_id, phone_e164)
        language = accounts[0].language if accounts else "en"
        for acc in accounts:
            await repos.drafts.cancel_pending_for_account(tenant_id, acc.id)
            for case in await repos.cases.for_account(tenant_id, acc.id):
                if case.status in OPEN_STATUSES:
                    await repos.cases.set(tenant_id, case.id, status="closed", closed_at=now)
                    await repos.follow_ups.cancel_for_case(tenant_id, case.id)
        if created:
            await audit.append(
                s,
                tenant_id,
                actor_type="debtor",
                actor_id="sms",
                action="opt_out.created",
                entity="opt_out",
                entity_id=None,
                payload={"source": source, "phone_e164": phone_e164},
            )
    if created:
        try:
            await sms.send(
                phone_e164,
                template("stop_confirmation", language),
                idempotency_key=f"stop:{tenant_id}:{phone_e164}",
            )
        except (
            SmsError
        ) as exc:  # the opt-out is effective regardless; the confirmation is best effort
            log.warning("stop confirmation not sent: %s", type(exc).__name__)
    return created


async def cancel_open_work(
    sm: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    now: datetime,
    reason: str,
) -> list[Case]:
    async with sm() as s, s.begin():
        repos = Repos(s)
        await repos.drafts.cancel_pending_for_account(tenant_id, account_id)
        closed = []
        for case in await repos.cases.for_account(tenant_id, account_id):
            if case.status in (
                "queued",
                "processing",
                "awaiting_approval",
                "scheduled",
                "requires_analyst",
            ):
                await repos.cases.set(tenant_id, case.id, status="closed", closed_at=now)
                await repos.follow_ups.cancel_for_case(tenant_id, case.id)
                closed.append(case)
        await audit.append(
            s,
            tenant_id,
            actor_type="system",
            actor_id="reply-graph",
            action="case.cancelled",
            entity="account",
            entity_id=str(account_id),
            payload={"reason": reason, "cases": len(closed)},
        )
        return closed

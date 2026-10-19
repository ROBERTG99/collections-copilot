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
CANCELLABLE = ("queued", "processing", "awaiting_approval", "scheduled", "requires_analyst")


def stop_confirmation_key(tenant_id: uuid.UUID, phone_e164: str) -> str:
    return f"stop:{tenant_id}:{phone_e164}"


async def apply_stop(
    sm: async_sessionmaker[AsyncSession],
    sms: SmsSender,
    *,
    tenant_id: uuid.UUID,
    phone_e164: str,
    now: datetime,
    source: str = "sms_stop",
    confirm: bool = True,
) -> bool:
    """Opt out, cancel pending drafts, close open cases, send ONE confirmation, audit.
    Returns True if this call created the opt-out (later STOPs are no-ops).

    The confirmation is an outbound `messages` row keyed `stop:<tenant>:<phone>`, written in the
    same transaction as the opt-out and sent from that row afterwards. A redelivery that finds
    the row never attempted (crash after the commit) sends it; a row already attempted is never
    resent (at-most-once, like every other SMS)."""
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
        key = stop_confirmation_key(tenant_id, phone_e164)
        if created and confirm:
            await repos.messages.claim(
                tenant_id,
                key,
                account_id=accounts[0].id if accounts else None,
                direction="outbound",
                to_e164=phone_e164,
                body=template("stop_confirmation", language),
                status="pending",
            )
            await audit.append(
                s,
                tenant_id,
                actor_type="debtor",
                actor_id="sms",
                action="opt_out.created",
                entity="opt_out",
                entity_id=None,
                payload={"source": source},
            )
        confirmation = await repos.messages.by_key(tenant_id, key)
        to_send: tuple[uuid.UUID, str] | None = None
        if confirmation and confirmation.status == "pending" and confirmation.attempts == 0:
            confirmation.attempts = 1  # committed before the provider call: never sent twice
            to_send = (confirmation.id, confirmation.body)
    if to_send is not None:
        await _send_confirmation(sm, sms, tenant_id, phone_e164, *to_send, key, now)
    return created


async def _send_confirmation(
    sm: async_sessionmaker[AsyncSession],
    sms: SmsSender,
    tenant_id: uuid.UUID,
    phone_e164: str,
    message_id: uuid.UUID,
    body: str,
    key: str,
    now: datetime,
) -> None:
    try:
        result = await sms.send(phone_e164, body, idempotency_key=key)
    except SmsError as exc:  # the opt-out is effective regardless; the confirmation is best effort
        log.warning("stop confirmation not sent: %s", type(exc).__name__)
        async with sm() as s, s.begin():
            await Repos(s).messages.set(
                tenant_id, message_id, status="failed", error=str(exc)[:500]
            )
        return
    async with sm() as s, s.begin():
        await Repos(s).messages.set(
            tenant_id,
            message_id,
            status=result.status,
            provider_sid=result.provider_sid,
            sent_at=now,
        )


async def cancel_open_work_in(
    repos: Repos, tenant_id: uuid.UUID, account_id: uuid.UUID, now: datetime, reason: str
) -> list[Case]:
    """Cancel pending drafts and close cancellable cases inside the caller's transaction."""
    await repos.drafts.cancel_pending_for_account(tenant_id, account_id)
    closed = []
    for case in await repos.cases.for_account(tenant_id, account_id):
        if case.status in CANCELLABLE:
            await repos.cases.set(tenant_id, case.id, status="closed", closed_at=now)
            await repos.follow_ups.cancel_for_case(tenant_id, case.id)
            closed.append(case)
    await audit.append(
        repos.session,
        tenant_id,
        actor_type="system",
        actor_id="reply-graph",
        action="case.cancelled",
        entity="account",
        entity_id=str(account_id),
        payload={"reason": reason, "cases": len(closed)},
    )
    return closed


async def cancel_open_work(
    sm: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    now: datetime,
    reason: str,
) -> list[Case]:
    async with sm() as s, s.begin():
        return await cancel_open_work_in(Repos(s), tenant_id, account_id, now, reason)

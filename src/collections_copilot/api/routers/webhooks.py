"""Twilio webhooks (FR-7, FR-8): status callbacks and inbound replies, verified by signature.

The signature is checked before anything else, so an unsigned request can neither probe which
numbers are debtors nor write to the database beyond a throttled audit event. Status callbacks
are stored once per (MessageSid, MessageStatus) and only move a message forward. A bad
signature on a status callback is audited (at most once per tenant and minute) under the tenant
its MessageSid belongs to; unknown or inbound ones are logged only.

Inbound replies carry only the sender's number. STOP keywords are applied by rule before anything
is queued, in EVERY tenant that has texted or holds that number (one confirmation SMS). Any other
reply is routed only when exactly one tenant has texted the number recently; otherwise it is not
attributed (each candidate tenant gets an audit event, without the text). See
docs/KNOWN_LIMITATIONS.md: production uses a sending number per tenant.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from redis.exceptions import RedisError

from collections_copilot.agent.reply_actions import apply_stop
from collections_copilot.api.errors import ProblemError
from collections_copilot.compliance.stop_keywords import is_stop
from collections_copilot.db import audit
from collections_copilot.db.repositories import Repos
from collections_copilot.integrations.queue.messages import ReplyMessage
from collections_copilot.integrations.queue.sqs import QueueUnavailableError
from collections_copilot.integrations.sms.signature import is_valid

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/webhooks/twilio", tags=["webhooks"])
MAX_BODY_CHARS = 1600
REPLY_ROUTING_WINDOW = timedelta(days=90)
STATUS_FIELDS = ("MessageSid", "MessageStatus", "ErrorCode", "AccountSid")
STATUS_RANK = {
    "accepted": 0,
    "queued": 0,
    "sending": 1,
    "sent": 2,
    "delivered": 3,
    "undelivered": 3,
    "failed": 3,
    "read": 4,
}


async def _form(request: Request) -> list[tuple[str, str]]:
    form = await request.form()
    return [(k, v) for k, v in form.multi_items() if isinstance(v, str)]


def _public_url(request: Request) -> str:
    return f"{request.app.state.settings.public_base_url.rstrip('/')}{request.url.path}"


def _signed(request: Request, params: list[tuple[str, str]]) -> bool:
    return is_valid(
        _public_url(request),
        params,
        request.headers.get("x-twilio-signature"),
        request.app.state.settings.twilio_auth_token,
    )


async def audit_bad_signature(request: Request, tenant: uuid.UUID | None, source: str) -> None:
    """Audit a rejected webhook at most once per tenant, source and minute (an attacker must not
    be able to flood the hash-chained log), with no attacker-supplied values."""
    if tenant is None:
        log.warning("webhook with a bad signature", extra={"source": source})
        return
    key = f"cc:badsig:{tenant}:{source}:{int(time.time() // 60)}"
    try:
        first = await request.app.state.redis.set(key, "1", nx=True, ex=120)
    except RedisError:
        first = True
    if not first:
        return
    async with request.app.state.sessionmaker() as s, s.begin():
        await audit.append(
            s,
            tenant,
            actor_type="webhook",
            actor_id=source,
            action="webhook.bad_signature",
            entity="webhook",
            entity_id=None,
            payload={"path": request.url.path},
        )


@router.post("/status", status_code=204, response_class=Response)
async def twilio_status(request: Request) -> Response:
    sm = request.app.state.sessionmaker
    params = await _form(request)
    fields = dict(params)
    sid, status = fields.get("MessageSid", ""), fields.get("MessageStatus", "").lower()
    valid = _signed(request, params)
    async with sm() as s:
        # MessageSids are random provider ids, so this lookup reveals nothing to a prober.
        message = await Repos(s).messages.by_sid(sid) if sid else None
    if not valid:
        await audit_bad_signature(request, message.tenant_id if message else None, "twilio")
        raise ProblemError(403, "invalid X-Twilio-Signature", code="bad_signature")
    if message is None or not status:
        return Response(status_code=204)  # unknown message: nothing to update
    kept = {k: fields[k] for k in STATUS_FIELDS if k in fields}  # no phone numbers at rest
    async with sm() as s, s.begin():
        repos = Repos(s)
        created = await repos.messages.add_status_event(message.tenant_id, sid, status, kept)
        current = await repos.messages.get(message.tenant_id, message.id)
        if (
            created
            and current is not None
            and STATUS_RANK.get(status, -1) > STATUS_RANK.get(current.status, -1)
        ):
            await repos.messages.set(
                message.tenant_id,
                message.id,
                status=status,
                error=fields.get("ErrorCode") or current.error,
            )
    return Response(status_code=204)


@router.post("/inbound", status_code=204, response_class=Response)
async def twilio_inbound(request: Request) -> Response:
    app = request.app
    sm = app.state.sessionmaker
    params = await _form(request)
    if not _signed(request, params):
        log.warning("inbound webhook with a bad signature")
        raise ProblemError(403, "invalid X-Twilio-Signature", code="bad_signature")
    fields = dict(params)
    sid, sender = fields.get("MessageSid", ""), fields.get("From", "")
    body = fields.get("Body", "")[:MAX_BODY_CHARS]
    if not sid or not sender:
        return Response(status_code=204)
    now = datetime.now(UTC)
    async with sm() as s:
        repos = Repos(s)
        routes = await repos.messages.latest_outbound_per_tenant(sender, now - REPLY_ROUTING_WINDOW)
        holders = await repos.accounts.tenants_with_phone(sender)
    if is_stop(body):
        await _stop_everywhere(request, sid, sender, body, routes, holders, now)
        return Response(status_code=204)
    if len(routes) != 1:
        await _unattributed(request, routes, sid)
        return Response(status_code=204)
    last = routes[0]
    tenant = last.tenant_id
    async with sm() as s, s.begin():
        message, _ = await Repos(s).messages.add_inbound(
            tenant,
            provider_sid=sid,
            account_id=last.account_id,
            case_id=last.case_id,
            from_e164=sender,
            to_e164=fields.get("To"),
            body=body,
            status="received",
        )
        message_id, handled = message.id, message.status == "handled"
    if handled:
        return Response(status_code=204)  # a retried webhook for a reply already handled
    reply = ReplyMessage(
        tenant_id=str(tenant),
        message_id=str(message_id),
        provider_sid=sid,
        from_e164=sender,
        body=body,
        received_at=now,
    )
    try:
        await app.state.queues.send(
            app.state.settings.sqs_replies_queue, reply.model_dump(mode="json", by_alias=True)
        )
    except QueueUnavailableError as exc:  # Twilio retries the webhook; the row is deduplicated
        raise ProblemError(503, "reply queue unavailable", code="queue_unavailable") from exc
    return Response(status_code=204)


async def _stop_everywhere(
    request: Request,
    sid: str,
    sender: str,
    body: str,
    routes: list[Any],
    holders: list[uuid.UUID],
    now: datetime,
) -> None:
    """A STOP binds every tenant that texted or holds the number; one confirmation is sent."""
    app = request.app
    sm = app.state.sessionmaker
    tenants = list(dict.fromkeys([r.tenant_id for r in routes] + holders))
    if not tenants:
        log.info("STOP from an unknown sender")
        return
    primary = routes[0] if len(routes) == 1 else None
    owner = primary.tenant_id if primary else tenants[0]
    for tenant in tenants:
        await apply_stop(
            sm,
            app.state.sms,
            tenant_id=tenant,
            phone_e164=sender,
            now=now,
            confirm=tenant == owner,
        )
    async with sm() as s, s.begin():
        repos = Repos(s)
        message, _ = await repos.messages.add_inbound(
            owner,
            provider_sid=sid,
            account_id=primary.account_id if primary else None,
            case_id=primary.case_id if primary else None,
            from_e164=sender,
            body=body,
            status="received",
        )
        await repos.messages.set(
            owner, message.id, classification="stop", classification_confidence=1.0
        )
        await repos.messages.mark_handled(owner, message.id)


async def _unattributed(request: Request, routes: list[Any], sid: str) -> None:
    """A reply we cannot attribute to one tenant is not stored as any tenant's message."""
    if not routes:
        log.info("inbound SMS from an unknown sender ignored")
        return
    async with request.app.state.sessionmaker() as s, s.begin():
        for route in routes:
            await audit.append(
                s,
                route.tenant_id,
                actor_type="webhook",
                actor_id="twilio",
                action="reply.unattributed",
                entity="account",
                entity_id=str(route.account_id) if route.account_id else None,
                payload={"reason": "number texted by several tenants", "candidates": len(routes)},
            )
    log.warning("inbound SMS matches several tenants; not routed", extra={"n": len(routes)})


mcpgw_router = APIRouter(prefix="/api/v1/webhooks/mcpgw", tags=["webhooks"])


class GatewayApproval(BaseModel):
    approval_id: str = Field(min_length=1, max_length=64)
    tenant: str
    tool: str
    args_redacted: dict[str, Any] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None


MCPGW_MAX_SKEW_S = 300


@mcpgw_router.post("/approvals", status_code=204, response_class=Response)
async def mcpgw_approval(request: Request) -> Response:
    """mcp-gateway held a ledger write for approval: link it to our promise and tell a
    supervisor. Signed with HMAC-SHA256 over `{timestamp}.{raw body}` (`X-MCPGW-Timestamp`,
    `X-MCPGW-Signature: sha256=...`); a timestamp more than 5 minutes off is refused, so a
    captured event cannot be replayed later."""
    app = request.app
    raw = await request.body()
    stamp = request.headers.get("x-mcpgw-timestamp", "")
    expected = (
        "sha256="
        + hmac.new(
            app.state.settings.mcpgw_webhook_secret.encode(),
            stamp.encode() + b"." + raw,
            hashlib.sha256,
        ).hexdigest()
    )
    given = request.headers.get("x-mcpgw-signature", "").encode("utf-8", "replace")
    fresh = stamp.isdigit() and abs(time.time() - int(stamp)) <= MCPGW_MAX_SKEW_S
    if not hmac.compare_digest(expected.encode(), given) or not fresh:
        await audit_bad_signature(request, _claimed_tenant(raw), "mcp-gateway")
        raise ProblemError(403, "invalid X-MCPGW-Signature", code="bad_signature")
    try:
        event = GatewayApproval.model_validate_json(raw)
        tenant = uuid.UUID(event.tenant)
    except (ValueError, TypeError) as exc:
        raise ProblemError(422, "invalid approval event", code="invalid_event") from exc
    sid = str(event.args_redacted.get("source_message_sid") or "")
    async with app.state.sessionmaker() as s, s.begin():
        repos = Repos(s)
        promise = await repos.promises.by_approval_id(event.approval_id)
        if promise is None and sid:
            # The webhook can arrive before the worker stored the approval id on the promise.
            promise = await repos.promises.by_key(tenant, f"ptp:{tenant}:{sid}")
        if promise is None or promise.tenant_id != tenant:
            raise ProblemError(404, "unknown approval", code="unknown_approval")
        # Serialise deliveries of one approval, then recognise a replay from the audit log
        # (durable: it holds while Redis is down).
        await s.refresh(promise, with_for_update=True)
        first = not await audit.has_event(
            s, tenant, "promise.approval_requested", str(promise.id), approval_id=event.approval_id
        )
        if promise.status == "pending":
            promise.status = "approval_required"
            promise.mcp_approval_id = event.approval_id
        case_ref = str(promise.case_id or promise.id)
        if not first:
            return Response(status_code=204)
        await audit.append(
            s,
            tenant,
            actor_type="webhook",
            actor_id="mcp-gateway",
            action="promise.approval_requested",
            entity="promise",
            entity_id=str(promise.id),
            payload={"approval_id": event.approval_id, "reasons": event.reasons},
        )
    await app.state.notifier.notify_supervisor(
        case_id=case_ref,
        reason=f"a promise-to-pay needs approval: {', '.join(event.reasons) or event.tool}",
        idempotency_key=f"mcpgw-approval:{event.approval_id}",
    )
    return Response(status_code=204)


def _claimed_tenant(raw: bytes) -> uuid.UUID | None:
    """The tenant an unverified gateway event names, for the throttled audit only."""
    try:
        return uuid.UUID(GatewayApproval.model_validate_json(raw).tenant)
    except (ValueError, TypeError):
        return None

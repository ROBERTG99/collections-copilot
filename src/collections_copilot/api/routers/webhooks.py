"""Twilio webhooks (FR-7, FR-8): status callbacks and inbound replies, verified by signature.

Status callbacks are stored once per (MessageSid, MessageStatus) and only move a message
forward. Inbound replies are stored once per MessageSid; STOP keywords are applied here by rule
(opt-out, cancel drafts, one confirmation) before anything is queued, so a STOP never reaches the
reply graph or a model. Everything else goes to `cc-replies`. A bad signature is 403 and audited
under the tenant the message resolves to.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

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


async def _reject(request: Request, tenant: uuid.UUID | None, kind: str, sid: str) -> None:
    """403 for a bad signature, audited when the message identifies a tenant."""
    if tenant is not None:
        async with request.app.state.sessionmaker() as s, s.begin():
            await audit.append(
                s,
                tenant,
                actor_type="webhook",
                actor_id="twilio",
                action="webhook.bad_signature",
                entity="message",
                entity_id=sid[:64] or None,
                payload={"kind": kind, "path": request.url.path},
            )
    log.warning("twilio webhook with a bad signature", extra={"kind": kind})
    raise ProblemError(403, "invalid X-Twilio-Signature", code="bad_signature")


@router.post("/status", status_code=204, response_class=Response)
async def twilio_status(request: Request) -> Response:
    sm = request.app.state.sessionmaker
    params = await _form(request)
    fields = dict(params)
    sid, status = fields.get("MessageSid", ""), fields.get("MessageStatus", "").lower()
    async with sm() as s:
        message = await Repos(s).messages.by_sid(sid) if sid else None
    valid = is_valid(
        _public_url(request),
        params,
        request.headers.get("x-twilio-signature"),
        request.app.state.settings.twilio_auth_token,
    )
    if not valid:
        await _reject(request, message.tenant_id if message else None, "status", sid)
    if message is None or not status:
        return Response(status_code=204)  # unknown message: nothing to update
    async with sm() as s, s.begin():
        repos = Repos(s)
        created = await repos.messages.add_status_event(message.tenant_id, sid, status, fields)
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
    fields = dict(params)
    sid, sender = fields.get("MessageSid", ""), fields.get("From", "")
    body = fields.get("Body", "")[:MAX_BODY_CHARS]
    async with sm() as s:
        # Inbound SMS carry only the sender's number: the latest outbound message to it
        # resolves the tenant, account and case.
        last = await Repos(s).messages.latest_outbound_to(sender) if sender else None
    valid = is_valid(
        _public_url(request),
        params,
        request.headers.get("x-twilio-signature"),
        app.state.settings.twilio_auth_token,
    )
    if not valid:
        await _reject(request, last.tenant_id if last else None, "inbound", sid)
    if last is None or not sid:
        log.info("inbound SMS from an unknown sender ignored")
        return Response(status_code=204)
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
    now = datetime.now(UTC)
    if is_stop(body):
        await apply_stop(sm, app.state.sms, tenant_id=tenant, phone_e164=sender, now=now)
        async with sm() as s, s.begin():
            repos = Repos(s)
            await repos.messages.set(
                tenant, message_id, classification="stop", classification_confidence=1.0
            )
            await repos.messages.mark_handled(tenant, message_id)
        return Response(status_code=204)
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


mcpgw_router = APIRouter(prefix="/api/v1/webhooks/mcpgw", tags=["webhooks"])


class GatewayApproval(BaseModel):
    approval_id: str = Field(min_length=1, max_length=64)
    tenant: str
    tool: str
    args_redacted: dict[str, Any] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None


@mcpgw_router.post("/approvals", status_code=204, response_class=Response)
async def mcpgw_approval(request: Request) -> Response:
    """mcp-gateway held a ledger write for approval: link it to our promise and tell a
    supervisor. Signed with HMAC-SHA256 over the raw body (`X-MCPGW-Signature: sha256=...`)."""
    app = request.app
    raw = await request.body()
    expected = (
        "sha256="
        + hmac.new(
            app.state.settings.mcpgw_webhook_secret.encode(), raw, hashlib.sha256
        ).hexdigest()
    )
    if not hmac.compare_digest(expected, request.headers.get("x-mcpgw-signature", "")):
        log.warning("mcp-gateway webhook with a bad signature")
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
        if promise.status == "pending":
            promise.status = "approval_required"
            promise.mcp_approval_id = event.approval_id
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
        case_ref = str(promise.case_id or promise.id)
    await app.state.notifier.notify_supervisor(
        case_id=case_ref,
        reason=f"a promise-to-pay needs approval: {', '.join(event.reasons) or event.tool}",
        idempotency_key=f"mcpgw-approval:{event.approval_id}",
    )
    return Response(status_code=204)

"""Reply graph (BUILD.md §4.5): stop_rule → classify_reply → route.

  stop (rule or model) → opt-out, cancel drafts and open cases, one confirmation SMS
  promise_to_pay → validate_promise (amount ≤ open balance, date 1–90 days) → record_promise → follow-up
  dispute → flag invoices, cancel drafts
  paid → payment-verification follow-up
  wrong_number → flag the contact, cancel drafts
  other → analyst

STOP never reaches the model. Each reply also resumes the case graph waiting in `await_reply`.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collections_copilot.agent.prompts import UntrustedNote, render
from collections_copilot.agent.reply_actions import apply_stop, cancel_open_work
from collections_copilot.agent.schemas import ReplyClassification
from collections_copilot.agent.services import Services
from collections_copilot.compliance.stop_keywords import is_stop
from collections_copilot.db import audit
from collections_copilot.db.repositories import Repos
from collections_copilot.integrations.queue.messages import TaskMessage
from collections_copilot.llm.redaction import AccountPII
from collections_copilot.observability.steps import node_context

MIN_PROMISE_DAYS = 1
MAX_PROMISE_DAYS = 90


class ReplyState(TypedDict, total=False):
    reply: dict[str, Any]
    tenant_id: str
    account: dict[str, Any]
    case_id: str | None
    label: str
    confidence: float
    promise: dict[str, Any] | None
    outcome: str
    promise_id: str


def build_reply_graph(services: Services, sm: async_sessionmaker[AsyncSession]) -> Any:
    def now() -> datetime:
        return services.clock()

    async def stop_rule(state: ReplyState) -> dict[str, Any]:
        reply = state["reply"]
        tenant = reply["tenant_id"]
        if not is_stop(reply["body"]):
            return {"tenant_id": tenant}
        with node_context("stop_rule"):
            await apply_stop(
                sm,
                services.sms,
                tenant_id=uuid.UUID(tenant),
                phone_e164=reply["from_e164"],
                now=now(),
            )
            await _set_message(state, "stop", 1.0, None)
        return {"tenant_id": tenant, "label": "stop", "confidence": 1.0, "outcome": "opted_out"}

    async def load(state: ReplyState) -> dict[str, Any]:
        reply = state["reply"]
        tenant = uuid.UUID(reply["tenant_id"])
        async with sm() as s:
            repos = Repos(s)
            msg = await repos.messages.get(tenant, reply["message_id"])
            if msg is None or msg.account_id is None:
                return {"account": {}, "case_id": None}
            acc = await repos.accounts.get(tenant, msg.account_id)
            assert acc is not None
            invoices = await repos.invoices.for_account(tenant, acc.id)
            open_inv = [i for i in invoices if i.status == "open"]
            return {
                "case_id": str(msg.case_id) if msg.case_id else None,
                "account": {
                    "id": str(acc.id),
                    "external_ref": acc.external_ref,
                    "name": acc.name,
                    "phone_e164": acc.phone_e164,
                    "language": acc.language,
                    "currency": acc.currency,
                    "open_balance_cents": sum(i.amount_cents for i in open_inv),
                    "open_invoices": [i.number for i in open_inv],
                },
            }

    async def classify_reply(state: ReplyState) -> dict[str, Any]:
        reply, acc = state["reply"], state.get("account") or {}
        today = now().date()
        rendered = render(
            "classify_reply",
            {
                "today": today.isoformat(),
                "language": acc.get("language", "en"),
                "currency": acc.get("currency", "USD"),
                "open_balance_cents": acc.get("open_balance_cents", 0),
            },
            [UntrustedNote(id=reply["message_id"], source="sms_reply", body=reply["body"])],
        )
        with node_context("classify_reply"):
            result = await services.gateway.structured(
                services.routes["classify_reply"],
                system=rendered.system,
                user=rendered.user,
                schema=ReplyClassification,
                prompt_version=f"{rendered.name}.{rendered.version}",
                prompt_hash=rendered.hash,
                pii=AccountPII(name=acc.get("name", ""), phone_e164=acc.get("phone_e164", "")),
                language=acc.get("language", "en"),
            )
        if result.ok and result.parsed is not None:
            out = result.parsed
            promise = out.promise.model_dump(mode="json") if out.promise else None
            label, confidence = out.label, out.confidence
        else:
            label, confidence, promise = "other", 0.0, None
        await _set_message(state, label, confidence, promise)
        return {"label": label, "confidence": confidence, "promise": promise}

    async def handle_stop(state: ReplyState) -> dict[str, Any]:
        reply = state["reply"]
        await apply_stop(
            sm,
            services.sms,
            tenant_id=uuid.UUID(reply["tenant_id"]),
            phone_e164=reply["from_e164"],
            now=now(),
            source="sms_reply_model",
        )
        return {"outcome": "opted_out"}

    async def validate_promise(state: ReplyState) -> dict[str, Any]:
        acc = state.get("account") or {}
        promise = dict(state.get("promise") or {})
        balance = int(acc.get("open_balance_cents", 0))
        amount = promise.get("amount_cents") or balance
        raw_date = promise.get("promised_date")
        problems = []
        if not acc or balance <= 0:
            problems.append("no open balance")
        if amount > balance:
            problems.append("amount above open balance")
        if not raw_date:
            problems.append("no promised date")
        else:
            delta = (date.fromisoformat(str(raw_date)) - now().date()).days
            if not MIN_PROMISE_DAYS <= delta <= MAX_PROMISE_DAYS:
                problems.append(
                    f"date {delta} days out (allowed {MIN_PROMISE_DAYS}–{MAX_PROMISE_DAYS})"
                )
        if problems:
            await _audit(state, "promise.invalid", {"problems": problems})
            return {"outcome": "analyst", "promise": {**promise, "problems": problems}}
        return {
            "promise": {"amount_cents": int(amount), "promised_date": str(raw_date)},
            "outcome": "valid",
        }

    async def record_promise(state: ReplyState) -> dict[str, Any]:
        from collections_copilot.agent.nodes.record_promise import record_promise as do_record

        return await do_record(services, sm, state)

    async def handle_dispute(state: ReplyState) -> dict[str, Any]:
        acc = state["account"]
        tenant = uuid.UUID(state["reply"]["tenant_id"])
        async with sm() as s, s.begin():
            repos = Repos(s)
            await repos.invoices.mark_disputed(
                tenant, uuid.UUID(acc["id"]), "disputed by SMS reply"
            )
            await repos.notes.add(
                tenant,
                uuid.UUID(acc["id"]),
                "sms_reply",
                "Debtor disputed by SMS; invoices paused.",
            )
        await cancel_open_work(sm, tenant, uuid.UUID(acc["id"]), now(), "dispute")
        return {"outcome": "disputed"}

    async def handle_paid(state: ReplyState) -> dict[str, Any]:
        acc = state["account"]
        tenant = uuid.UUID(state["reply"]["tenant_id"])
        async with sm() as s, s.begin():
            repos = Repos(s)
            await repos.follow_ups.add(
                tenant,
                case_id=uuid.UUID(state["case_id"]) if state.get("case_id") else None,
                account_id=uuid.UUID(acc["id"]),
                kind="payment_verification",
                due_at=now() + timedelta(days=2),
                payload={"message_id": state["reply"]["message_id"]},
            )
            await repos.notes.add(
                tenant,
                uuid.UUID(acc["id"]),
                "sms_reply",
                "Debtor reports payment; verify remittance.",
            )
        return {"outcome": "verify_payment"}

    async def handle_wrong_number(state: ReplyState) -> dict[str, Any]:
        acc = state["account"]
        tenant = uuid.UUID(state["reply"]["tenant_id"])
        async with sm() as s, s.begin():
            await Repos(s).accounts.set_contact_flag(tenant, uuid.UUID(acc["id"]), "wrong_number")
        await cancel_open_work(sm, tenant, uuid.UUID(acc["id"]), now(), "wrong_number")
        return {"outcome": "contact_flagged"}

    async def to_analyst(state: ReplyState) -> dict[str, Any]:
        await _audit(state, "reply.needs_analyst", {"label": state.get("label")})
        return {"outcome": "analyst"}

    async def notify_case(state: ReplyState) -> dict[str, Any]:
        case_id = state.get("case_id")
        enqueue = services.enqueue
        if case_id and callable(enqueue):
            await enqueue(
                TaskMessage(
                    tenant_id=state["reply"]["tenant_id"],
                    kind="resume_case",
                    case_id=case_id,
                    resume={"kind": "await_reply", "event": state.get("label", "reply")},
                )
            )
        return {}

    async def _set_message(
        state: ReplyState, label: str, confidence: float, promise: dict[str, Any] | None
    ) -> None:
        reply = state["reply"]
        async with sm() as s, s.begin():
            await Repos(s).messages.set(
                uuid.UUID(reply["tenant_id"]),
                uuid.UUID(reply["message_id"]),
                classification=label,
                classification_confidence=confidence,
                extracted={"promise": promise} if promise else None,
            )

    async def _audit(state: ReplyState, action: str, payload: dict[str, Any]) -> None:
        reply = state["reply"]
        async with sm() as s, s.begin():
            await audit.append(
                s,
                uuid.UUID(reply["tenant_id"]),
                actor_type="system",
                actor_id="reply-graph",
                action=action,
                entity="message",
                entity_id=reply["message_id"],
                payload=payload,
            )

    def after_stop(state: ReplyState) -> str:
        return END if state.get("label") == "stop" else "load"

    def after_load(state: ReplyState) -> str:
        return "classify_reply" if state.get("account") else "to_analyst"

    def after_classify(state: ReplyState) -> str:
        return {
            "stop": "handle_stop",
            "promise_to_pay": "validate_promise",
            "dispute": "handle_dispute",
            "paid": "handle_paid",
            "wrong_number": "handle_wrong_number",
        }.get(state.get("label", "other"), "to_analyst")

    def after_validate(state: ReplyState) -> str:
        return "record_promise" if state.get("outcome") == "valid" else "to_analyst"

    g: StateGraph[ReplyState] = StateGraph(ReplyState)
    for name, fn in [
        ("stop_rule", stop_rule),
        ("load", load),
        ("classify_reply", classify_reply),
        ("handle_stop", handle_stop),
        ("validate_promise", validate_promise),
        ("record_promise", record_promise),
        ("handle_dispute", handle_dispute),
        ("handle_paid", handle_paid),
        ("handle_wrong_number", handle_wrong_number),
        ("to_analyst", to_analyst),
        ("notify_case", notify_case),
    ]:
        g.add_node(name, fn)  # type: ignore[call-overload]
    g.add_edge(START, "stop_rule")
    g.add_conditional_edges("stop_rule", after_stop, ["load", END])
    g.add_conditional_edges("load", after_load, ["classify_reply", "to_analyst"])
    g.add_conditional_edges(
        "classify_reply",
        after_classify,
        [
            "handle_stop",
            "validate_promise",
            "handle_dispute",
            "handle_paid",
            "handle_wrong_number",
            "to_analyst",
        ],
    )
    g.add_conditional_edges("validate_promise", after_validate, ["record_promise", "to_analyst"])
    for name in (
        "handle_stop",
        "record_promise",
        "handle_dispute",
        "handle_paid",
        "handle_wrong_number",
        "to_analyst",
    ):
        g.add_edge(name, "notify_case")
    g.add_edge("notify_case", END)
    return g.compile()

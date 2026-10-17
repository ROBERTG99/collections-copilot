"""Reply graph (BUILD.md §4.5): stop_rule → classify_reply → route.

  stop (rule or model) → opt-out, cancel drafts and open cases, one confirmation SMS
  promise_to_pay → validate_promise (amount ≤ open balance, date 1–90 days) → record_promise → follow-up
  dispute → flag invoices, cancel drafts
  paid → payment-verification follow-up
  wrong_number → flag the contact, cancel drafts
  other → analyst

STOP never reaches the model. Each reply also resumes the case graph waiting in `await_reply`.

Redelivery (SQS is at-least-once): each handler applies its side effects and marks the message
`handled` in one transaction, so they happen once; a classification already stored on the
message is reused instead of calling the model again; a handled message skips straight to
`notify_case` (resuming the case graph is itself idempotent).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collections_copilot.agent.prompts import UntrustedNote, render
from collections_copilot.agent.reply_actions import apply_stop, cancel_open_work_in
from collections_copilot.agent.schemas import ReplyClassification
from collections_copilot.agent.services import Services
from collections_copilot.compliance.rules import amounts
from collections_copilot.compliance.stop_keywords import is_stop
from collections_copilot.db import audit
from collections_copilot.db.repositories import Repos
from collections_copilot.integrations.queue.messages import TaskMessage
from collections_copilot.llm.redaction import AccountPII
from collections_copilot.observability import metrics, tracing
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
    previous: dict[str, Any] | None


def _traced(
    name: str, fn: Callable[[ReplyState], Awaitable[dict[str, Any]]]
) -> Callable[[ReplyState], Awaitable[dict[str, Any]]]:
    """One span and one duration sample per reply-graph node (identifiers only, never text)."""

    async def wrapped(state: ReplyState) -> dict[str, Any]:
        started = time.perf_counter()
        status = "ok"
        attrs = {
            "cc.graph": "reply",
            "cc.node": name,
            "cc.tenant_id": str(state.get("tenant_id", "")),
        }
        with tracing.tracer().start_as_current_span(f"node {name}", attributes=attrs) as span:
            try:
                return await fn(state)
            except Exception:
                status = "error"
                span.set_status(tracing.Status(tracing.StatusCode.ERROR))
                raise
            finally:
                span.set_attribute("cc.status", status)
                metrics.NODE_DURATION.labels("reply", name, status).observe(
                    time.perf_counter() - started
                )

    return wrapped


async def classify_text(
    services: Services,
    *,
    message_id: str,
    body: str,
    account: dict[str, Any],
    today: date,
) -> tuple[str, float, dict[str, Any] | None, str]:
    """The model classification of a non-STOP reply (also the replies eval target's code path).

    Returns (label, confidence, promise, source): source is "model", or "fallback" when the
    model failed and the reply goes to an analyst as "other"."""
    rendered = render(
        "classify_reply",
        {
            "today": today.isoformat(),
            "language": account.get("language", "en"),
            "currency": account.get("currency", "USD"),
            "open_balance_cents": account.get("open_balance_cents", 0),
        },
        [UntrustedNote(id=message_id, source="sms_reply", body=body)],
    )
    with node_context("classify_reply"):
        result = await services.gateway.structured(
            services.routes["classify_reply"],
            system=rendered.system,
            user=rendered.user,
            schema=ReplyClassification,
            prompt_version=f"{rendered.name}.{rendered.version}",
            prompt_hash=rendered.hash,
            pii=AccountPII(name=account.get("name", ""), phone_e164=account.get("phone_e164", "")),
            language=account.get("language", "en"),
        )
    if result.ok and result.parsed is not None:
        out = result.parsed
        promise = out.promise.model_dump(mode="json") if out.promise else None
        return out.label, out.confidence, promise, "model"
    return "other", 0.0, None, "fallback"


def promise_problems(
    promise: dict[str, Any] | None,
    balance_cents: int,
    today: date,
    body: str | None = None,
) -> list[str]:
    """Why an extracted promise may not be written back (empty when it may).

    With the reply `body`, the amount must also be one the debtor actually wrote: code, not the
    model, decides what reaches the ledger (a date such as 2026-03-16 is never an amount).
    """
    promise = promise or {}
    amount = promise.get("amount_cents")
    raw_date = promise.get("promised_date")
    problems = []
    if balance_cents <= 0:
        problems.append("no open balance")
    if not amount or amount <= 0:
        problems.append("no amount")  # never assume the full balance was promised
    elif amount > balance_cents:
        problems.append("amount above open balance")
    elif body is not None and amount not in {c for _, c, _ in amounts.extract(body)}:
        problems.append("amount not stated in the reply")
    if not raw_date:
        problems.append("no promised date")
    else:
        delta = (date.fromisoformat(str(raw_date)) - today).days
        if not MIN_PROMISE_DAYS <= delta <= MAX_PROMISE_DAYS:
            problems.append(
                f"date {delta} days out (allowed {MIN_PROMISE_DAYS}–{MAX_PROMISE_DAYS})"
            )
    return problems


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
            await _set_message(state, "stop", 1.0, None, "rule")
        return {"tenant_id": tenant, "label": "stop", "confidence": 1.0, "outcome": "opted_out"}

    async def load(state: ReplyState) -> dict[str, Any]:
        reply = state["reply"]
        tenant = uuid.UUID(reply["tenant_id"])
        async with sm() as s:
            repos = Repos(s)
            msg = await repos.messages.get(tenant, reply["message_id"])
            if msg is None or msg.account_id is None:
                return {"account": {}, "case_id": None}
            case_id = str(msg.case_id) if msg.case_id else None
            if msg.status == "handled":
                return {
                    "account": {},
                    "case_id": case_id,
                    "label": msg.classification or "other",
                    "outcome": "duplicate",
                }
            previous = (
                {
                    "label": msg.classification,
                    "confidence": msg.classification_confidence or 0.0,
                    "promise": (msg.extracted or {}).get("promise"),
                }
                if msg.classification
                else None
            )
            acc = await repos.accounts.get(tenant, msg.account_id)
            assert acc is not None
            invoices = await repos.invoices.for_account(tenant, acc.id)
            open_inv = [i for i in invoices if i.status == "open"]
            return {
                "case_id": case_id,
                "previous": previous,
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
        previous = state.get("previous")
        if previous:  # redelivery after a crash: never classify the same reply twice
            return {
                "label": previous["label"],
                "confidence": previous["confidence"],
                "promise": previous["promise"],
            }
        label, confidence, promise, source = await classify_text(
            services,
            message_id=reply["message_id"],
            body=reply["body"],
            account=acc,
            today=now().date(),
        )
        await _set_message(state, label, confidence, promise, source)
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
        problems = promise_problems(
            promise,
            int(acc.get("open_balance_cents", 0)),
            now().date(),
            body=state["reply"]["body"],
        )
        if problems:
            return {"outcome": "analyst", "promise": {**promise, "problems": problems}}
        return {
            "promise": {
                "amount_cents": int(promise["amount_cents"]),
                "promised_date": str(promise["promised_date"]),
            },
            "outcome": "valid",
        }

    async def record_promise(state: ReplyState) -> dict[str, Any]:
        from collections_copilot.agent.nodes.record_promise import record_promise as do_record

        return await do_record(services, sm, state)

    async def handle_dispute(state: ReplyState) -> dict[str, Any]:
        acc = state["account"]
        tenant, account_id = uuid.UUID(state["reply"]["tenant_id"]), uuid.UUID(acc["id"])
        async with sm() as s, s.begin():
            repos = Repos(s)
            if not await _claim(repos, state):
                return {"outcome": "duplicate"}
            await repos.invoices.mark_disputed(tenant, account_id, "disputed by SMS reply")
            await repos.notes.add(
                tenant, account_id, "sms_reply", "Debtor disputed by SMS; invoices paused."
            )
            await cancel_open_work_in(repos, tenant, account_id, now(), "dispute")
        return {"outcome": "disputed"}

    async def handle_paid(state: ReplyState) -> dict[str, Any]:
        acc = state["account"]
        tenant, account_id = uuid.UUID(state["reply"]["tenant_id"]), uuid.UUID(acc["id"])
        async with sm() as s, s.begin():
            repos = Repos(s)
            if not await _claim(repos, state):
                return {"outcome": "duplicate"}
            await repos.follow_ups.add(
                tenant,
                case_id=uuid.UUID(state["case_id"]) if state.get("case_id") else None,
                account_id=account_id,
                kind="payment_verification",
                due_at=now() + timedelta(days=2),
                payload={"message_id": state["reply"]["message_id"]},
            )
            await repos.notes.add(
                tenant, account_id, "sms_reply", "Debtor reports payment; verify remittance."
            )
        return {"outcome": "verify_payment"}

    async def handle_wrong_number(state: ReplyState) -> dict[str, Any]:
        acc = state["account"]
        tenant, account_id = uuid.UUID(state["reply"]["tenant_id"]), uuid.UUID(acc["id"])
        async with sm() as s, s.begin():
            repos = Repos(s)
            if not await _claim(repos, state):
                return {"outcome": "duplicate"}
            await repos.accounts.set_contact_flag(tenant, account_id, "wrong_number")
            await cancel_open_work_in(repos, tenant, account_id, now(), "wrong_number")
        return {"outcome": "contact_flagged"}

    async def to_analyst(state: ReplyState) -> dict[str, Any]:
        reply = state["reply"]
        payload: dict[str, Any] = {"label": state.get("label")}
        problems = (state.get("promise") or {}).get("problems")
        if problems:
            payload["problems"] = problems
        async with sm() as s, s.begin():
            repos = Repos(s)
            if await _claim(repos, state):
                await audit.append(
                    s,
                    uuid.UUID(reply["tenant_id"]),
                    actor_type="system",
                    actor_id="reply-graph",
                    action="reply.needs_analyst",
                    entity="message",
                    entity_id=reply["message_id"],
                    payload=payload,
                )
        return {"outcome": "analyst"}

    async def notify_case(state: ReplyState) -> dict[str, Any]:
        async with sm() as s, s.begin():  # stop and promise paths are idempotent on their own
            await _claim(Repos(s), state)
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
        state: ReplyState,
        label: str,
        confidence: float,
        promise: dict[str, Any] | None,
        source: str,
    ) -> None:
        reply = state["reply"]
        async with sm() as s, s.begin():
            await Repos(s).messages.set(
                uuid.UUID(reply["tenant_id"]),
                uuid.UUID(reply["message_id"]),
                classification=label,
                classification_confidence=confidence,
                extracted={"promise": promise, "source": source},
            )

    async def _claim(repos: Repos, state: ReplyState) -> bool:
        reply = state["reply"]
        return await repos.messages.mark_handled(
            uuid.UUID(reply["tenant_id"]), uuid.UUID(reply["message_id"])
        )

    def after_stop(state: ReplyState) -> str:
        return END if state.get("label") == "stop" else "load"

    def after_load(state: ReplyState) -> str:
        if state.get("outcome") == "duplicate":
            return "notify_case"
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
        g.add_node(name, _traced(name, fn))  # type: ignore[call-overload]
    g.add_edge(START, "stop_rule")
    g.add_conditional_edges("stop_rule", after_stop, ["load", END])
    g.add_conditional_edges("load", after_load, ["classify_reply", "to_analyst", "notify_case"])
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

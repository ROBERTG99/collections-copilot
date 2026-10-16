"""Case graph (BUILD.md §4.5).

load_account → prioritize → compliance_precheck → next_best_action → route
  pause | no_action → close
  escalate → notify_supervisor → close
  remind | payment_plan → draft → compliance_postcheck
      failed once → draft again with the violations; failed twice → human_approval (requires_edit)
  human_approval: interrupt() unless the auto-approval policy matches; resume {decision, body, reason, actor}
      approve | edit → final post-check → wait_send_window → send_sms → schedule_follow_up → await_reply (interrupt)
      reject → close

Checkpointer: AsyncPostgresSaver with thread_id = case_id. Nodes are idempotent because LangGraph re-runs
a node from its start after a crash or a resume. No SMS is ever sent without an approval record
(human or auto) for the exact draft version being sent (NFR-5).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from collections_copilot.agent import policy as auto_policy
from collections_copilot.agent.nba_rules import RuleFacts
from collections_copilot.agent.nodes.draft import DraftInput, make_draft
from collections_copilot.agent.nodes.nba import NbaInput, decide_nba
from collections_copilot.agent.nodes.rationale import priority_rationale
from collections_copilot.agent.prompts import UntrustedNote
from collections_copilot.agent.services import Services
from collections_copilot.agent.store import CaseFacts, business_days_after
from collections_copilot.compliance import engine
from collections_copilot.compliance.engine import AccountFacts, ComplianceContext, InvoiceFacts
from collections_copilot.integrations.sms.base import SmsError
from collections_copilot.llm.redaction import AccountPII
from collections_copilot.observability.steps import StepRecord, current_context, node_context
from collections_copilot.scoring.priority import inputs_from, score

SEND_RETRIES = 3
APPROVED = ("approve", "edit", "auto_approve")


class CaseState(TypedDict, total=False):
    case_id: str
    tenant_id: str
    run_id: str
    now: str  # fixed clock for evals; absent in production
    input_facts: dict[str, Any]  # evals only
    facts: dict[str, Any]
    priority: dict[str, Any]
    precheck: dict[str, Any]
    nba: dict[str, Any]
    draft: dict[str, Any]
    postcheck: dict[str, Any]
    redrafts: int
    requires_edit: bool
    approval: dict[str, Any]
    send: dict[str, Any]
    status: str
    closed_reason: str
    fallback: bool
    errors: list[str]


Node = Callable[[CaseState], Awaitable[dict[str, Any]]]


def _compliance_ctx(
    facts: CaseFacts,
    now: datetime,
    action: str | None,
    body: str | None,
    contacts: list[datetime] | None = None,
) -> ComplianceContext:
    return ComplianceContext(
        now=now,
        account=AccountFacts(
            facts.timezone,
            facts.language,
            facts.currency,
            facts.opted_out,
        ),
        invoices=[
            InvoiceFacts(i.number, i.amount_cents, i.currency, i.status) for i in facts.invoices
        ],
        contacts_7d=contacts
        if contacts is not None
        else [datetime.fromisoformat(c) for c in facts.contacts_7d],
        action=action,
        body=body,
        account_name=facts.name,
        contact_flag=facts.contact_flag,
    )


def build_case_graph(services: Services, checkpointer: BaseCheckpointSaver[Any] | None) -> Any:
    store = services.store
    settings = services.settings

    def now_of(state: CaseState) -> datetime:
        return datetime.fromisoformat(state["now"]) if state.get("now") else services.clock()

    def facts_of(state: CaseState) -> CaseFacts:
        return CaseFacts.from_state(state["facts"])

    def node(name: str, fn: Node) -> Node:
        """Wrap a node: step record, SSE `node` event, and the step context for LLM calls."""

        async def wrapped(state: CaseState) -> dict[str, Any]:
            started = time.perf_counter()
            status = "ok"
            with node_context(name):
                await services.events.publish(
                    state["tenant_id"], state["case_id"], "node", {"node": name, "phase": "start"}
                )
                try:
                    return await fn(state)
                except Exception:
                    status = "error"
                    raise
                finally:
                    ctx = current_context()
                    latency = (time.perf_counter() - started) * 1000
                    await services.gateway.sink.record(
                        StepRecord(
                            run_id=ctx.run_id or state.get("run_id", ""),
                            task_id=ctx.task_id,
                            tenant_id=state["tenant_id"],
                            case_id=state["case_id"],
                            node=name,
                            kind="node",
                            latency_ms=latency,
                            status=status,  # type: ignore[arg-type]
                        )
                    )
                    await services.events.publish(
                        state["tenant_id"],
                        state["case_id"],
                        "node",
                        {
                            "node": name,
                            "phase": "end",
                            "status": status,
                            "latency_ms": round(latency, 1),
                        },
                    )

        return wrapped

    async def set_status(state: CaseState, status: str, **fields: Any) -> None:
        await store.set_case(state, status=status, **fields)
        await services.events.publish(
            state["tenant_id"], state["case_id"], "status", {"status": status}
        )

    # --- nodes ------------------------------------------------------------------------------
    async def load_account(state: CaseState) -> dict[str, Any]:
        facts = await store.load(dict(state), now_of(state))
        await set_status(state, "processing")
        return {
            "facts": facts.as_state(),
            "redrafts": 0,
            "requires_edit": False,
            "errors": [],
            "fallback": False,
        }

    async def prioritize(state: CaseState) -> dict[str, Any]:
        f = facts_of(state)
        s = score(
            inputs_from(
                invoices=[(i.amount_cents, i.status, i.days_past_due) for i in f.invoices],
                broken_promises_90d=f.broken_promises_90d,
                segment=f.segment,
                currency=f.currency,
            )
        )
        rationale = await priority_rationale(services.gateway, services.routes, s)
        await store.set_case(
            state,
            priority_score=s.score,
            score_components=s.components,
            priority_rationale=rationale,
        )
        return {"priority": {"score": s.score, "components": s.components, "rationale": rationale}}

    async def compliance_precheck(state: CaseState) -> dict[str, Any]:
        f = facts_of(state)
        result = engine.precheck(_compliance_ctx(f, now_of(state), None, None))
        await store.set_case(state, precheck=result.as_dict())
        return {"precheck": result.as_dict()}

    async def next_best_action(state: CaseState) -> dict[str, Any]:
        f = facts_of(state)
        open_inv = [i for i in f.invoices if i.status == "open"]
        decision = await decide_nba(
            services.gateway,
            services.routes,
            NbaInput(
                facts=RuleFacts(
                    open_balance_cents=sum(i.amount_cents for i in open_inv),
                    max_days_past_due=max((i.days_past_due for i in open_inv), default=0),
                    broken_promises_90d=f.broken_promises_90d,
                    open_invoices=len(open_inv),
                    disputed_invoices=sum(1 for i in f.invoices if i.status == "disputed"),
                    currency=f.currency,
                ),
                segment=f.segment,
                language=f.language,
                notes=[UntrustedNote(n.id, n.source, n.body, n.created_at) for n in f.notes],
                pii=AccountPII(name=f.name, phone_e164=f.phone_e164),
            ),
        )
        cited = [n for n in decision.cited_note_ids]
        await store.set_case(
            state,
            nba_action=decision.action,
            nba_confidence=decision.confidence,
            nba_rationale=decision.rationale,
            cited_note_ids=[c for c in cited if _is_uuid(c)],
            fallback=decision.fallback,
        )
        nba = {
            "action": decision.action,
            "confidence": decision.confidence,
            "rationale": decision.rationale,
            "cited_note_ids": cited,
            "source": decision.source,
            "requires_analyst": decision.requires_analyst,
            "analyst_reason": decision.analyst_reason,
        }
        if decision.requires_analyst:
            await set_status(state, "requires_analyst", analyst_reason=decision.analyst_reason)
        return {"nba": nba, "fallback": state.get("fallback", False) or decision.fallback}

    async def notify_supervisor(state: CaseState) -> dict[str, Any]:
        await services.notifier.notify_supervisor(
            case_id=state["case_id"],
            reason=state["nba"]["rationale"],
            idempotency_key=f"esc:{state['case_id']}",
        )
        await store.audit(
            state, "case.escalated", "case", state["case_id"], {"reason": state["nba"]["rationale"]}
        )
        return {"closed_reason": "escalated"}

    async def draft(state: CaseState) -> dict[str, Any]:
        f = facts_of(state)
        version = int(state.get("redrafts", 0)) + 1
        feedback = (
            [
                f"{v['code']}: {v['message']}"
                for v in (state.get("postcheck") or {}).get("violations", [])
                if v.get("blocking")
            ]
            if version > 1
            else []
        )
        stored = await store.draft_version(dict(state), version)
        if stored is not None and stored.get("status") != "cancelled":
            # A re-run after a crash keeps the row already written for this version, so the
            # text approved and sent is always the stored text (LLMs are not deterministic).
            d = {
                "version": version,
                "body": stored["body"],
                "author": stored["author"],
                "model": stored.get("model"),
                "prompt_version": stored.get("prompt_version"),
            }
            return {
                "draft": d,
                "fallback": state.get("fallback", False) or stored["author"] == "fallback",
            }
        result = await make_draft(
            services.gateway,
            services.routes,
            DraftInput(
                action=state["nba"]["action"],
                language=f.language,
                account_name=f.name,
                currency=f.currency,
                invoices=[
                    InvoiceFacts(i.number, i.amount_cents, i.currency, i.status) for i in f.invoices
                ],
                pay_link=f"{services.pay_link_base}/{state['case_id'][:8]}",
                pii=AccountPII(name=f.name, phone_e164=f.phone_e164),
                feedback=feedback,
            ),
        )
        d = {
            "version": version,
            "body": result.body,
            "author": result.author,
            "model": result.model,
            "prompt_version": result.prompt_version,
        }
        await store.save_draft(
            state,
            version=version,
            body=result.body,
            author=result.author,
            language=f.language,
            model=result.model,
            prompt_version=result.prompt_version,
            precheck=state.get("precheck"),
            postcheck=None,
        )
        return {"draft": d, "fallback": state.get("fallback", False) or result.author == "fallback"}

    async def compliance_postcheck(state: CaseState) -> dict[str, Any]:
        f = facts_of(state)
        d = state["draft"]
        result = engine.postcheck(
            _compliance_ctx(f, now_of(state), state["nba"]["action"], d["body"])
        )
        await store.save_draft(
            state,
            version=d["version"],
            body=d["body"],
            author=d["author"],
            language=f.language,
            model=d.get("model"),
            prompt_version=d.get("prompt_version"),
            precheck=state.get("precheck"),
            postcheck=result.as_dict(),
        )
        update: dict[str, Any] = {"postcheck": result.as_dict()}
        if not result.ok:
            update["redrafts"] = int(state.get("redrafts", 0)) + 1
            if update["redrafts"] >= 2:
                update["requires_edit"] = True
                await store.set_case(state, requires_edit=True)
        return update

    async def human_approval(state: CaseState) -> dict[str, Any]:
        f = facts_of(state)
        d = state["draft"]
        post = state["postcheck"]
        effective = auto_policy.effective(
            f.auto_policy, settings.auto_approve_max_usd, settings.auto_approve_min_confidence
        )
        open_inv = [i for i in f.invoices if i.status == "open"]
        ok, reason = auto_policy.qualifies(
            effective,
            action=state["nba"]["action"],
            open_balance_cents=sum(i.amount_cents for i in open_inv),
            currency=f.currency,
            confidence=float(state["nba"]["confidence"]),
            postcheck_ok=bool(post.get("ok")),
            requires_edit=bool(state.get("requires_edit")),
            invoice_currencies=[i.currency for i in open_inv],
        )
        if ok and state["nba"].get("source") == "llm":
            send_at = datetime.fromisoformat(post["send_at"])
            await store.record_auto_approval(state, int(d["version"]), effective.as_dict(), send_at)
            await services.events.publish(
                state["tenant_id"],
                state["case_id"],
                "draft",
                {"version": d["version"], "auto_approved": True},
            )
            return {
                "approval": {
                    "decision": "auto_approve",
                    "version": d["version"],
                    "actor": "system",
                    "reason": reason,
                    "send_at": post["send_at"],
                }
            }
        await set_status(state, "awaiting_approval")
        await services.events.publish(
            state["tenant_id"],
            state["case_id"],
            "draft",
            {"version": d["version"], "requires_edit": bool(state.get("requires_edit"))},
        )
        decision = interrupt(
            {
                "kind": "approval",
                "draft_version": d["version"],
                "requires_edit": bool(state.get("requires_edit")),
                "auto_approval": reason,
            }
        )
        if not isinstance(decision, dict) or decision.get("decision") not in (
            "approve",
            "edit",
            "reject",
        ):
            return {"approval": {"decision": "invalid"}}
        if decision["decision"] == "reject":
            return {
                "approval": {
                    "decision": "reject",
                    "actor": decision.get("actor"),
                    "reason": decision.get("reason"),
                },
                "closed_reason": "rejected",
            }
        latest = await store.latest_draft(dict(state))
        if latest is None:
            return {"approval": {"decision": "invalid"}}
        # Re-read everything: the case may have been closed (STOP, dispute) and the debtor's
        # invoices or opt-out may have changed while the draft waited for a human.
        snap = await store.send_snapshot(dict(state), int(latest["version"]), now_of(state))
        if snap.case_closed:
            return {"approval": {"decision": "cancelled"}, "closed_reason": "case_closed"}
        approval = snap.approval
        if snap.draft_cancelled or approval is None or approval["decision"] not in APPROVED:
            return {"approval": {"decision": "invalid"}}
        assert snap.draft is not None
        latest = snap.draft
        final = engine.postcheck(
            _compliance_ctx(snap.facts, now_of(state), state["nba"]["action"], latest["body"])
        )
        if not final.ok:
            return {
                "approval": {"decision": "invalid"},
                "postcheck": final.as_dict(),
                "requires_edit": True,
            }
        return {
            "draft": {
                **d,
                "version": int(latest["version"]),
                "body": latest["body"],
                "author": latest["author"],
            },
            "postcheck": final.as_dict(),
            "approval": {
                "decision": approval["decision"],
                "version": int(latest["version"]),
                "actor": approval["decided_by"],
                "send_at": approval.get("send_at") or final.send_at.isoformat(),
            },
        }

    async def wait_send_window(state: CaseState) -> dict[str, Any]:
        send_at = datetime.fromisoformat(state["approval"]["send_at"])
        if send_at > now_of(state):
            await store.follow_up(
                state, "deferred_send", send_at, {"draft_version": state["draft"]["version"]}
            )
            await set_status(state, "scheduled")
            interrupt({"kind": "deferred_send", "send_at": send_at.isoformat()})
        return {}

    async def send_sms(state: CaseState) -> dict[str, Any]:
        version = int(state["draft"]["version"])
        now = now_of(state)
        # Never trust the checkpoint at send time: re-read the case, the draft row, its
        # approval record and the debtor's facts (disputes, opt-out, cap, contact flag).
        snap = await store.send_snapshot(dict(state), version, now)
        f = snap.facts
        approval = snap.approval
        blocked = (
            "case_closed"
            if snap.case_closed
            else "draft_cancelled"
            if snap.draft_cancelled
            else "no_approval_record"
            if approval is None or approval["decision"] not in APPROVED
            else None
        )
        if blocked:
            await store.audit(state, "sms.blocked", "case", state["case_id"], {"reason": blocked})
            return {"send": {"status": "blocked", "reason": blocked}, "closed_reason": blocked}
        assert snap.draft is not None
        assert approval is not None
        body = str(snap.draft["body"])
        result = engine.postcheck(_compliance_ctx(f, now, state["nba"]["action"], body))
        if not result.ok:
            codes = [v.code for v in result.violations if v.blocking]
            await store.audit(state, "sms.blocked", "case", state["case_id"], {"violations": codes})
            return {
                "send": {"status": "blocked", "violations": codes},
                "closed_reason": "compliance_blocked",
            }
        if result.deferred:
            return {
                "approval": {**state["approval"], "send_at": result.send_at.isoformat()},
                "send": {"status": "deferred"},
            }
        key = f"sms:{state['case_id']}:{version}"
        claim = await store.claim_message(dict(state), key, f.phone_e164, body, version)
        if not claim["created"]:
            if claim["provider_sid"]:
                # Crash after the provider accepted it: make sure the 7-day cap counts it.
                await store.add_contact(state, claim["id"], now)
                return {
                    "send": {
                        "status": "sent",
                        "provider_sid": claim["provider_sid"],
                        "message_id": claim["id"],
                    }
                }
            # Crash between claim and provider response: at-most-once, never resend blindly.
            await store.mark_message(
                state, claim["id"], status="failed", error="unknown_after_restart"
            )
            await store.follow_up(
                state, "send_retry", now + timedelta(hours=1), {"reason": "unknown_after_restart"}
            )
            await set_status(state, "failed")
            return {
                "send": {"status": "failed", "reason": "unknown_after_restart"},
                "closed_reason": "send_failed",
            }
        last_error = ""
        for attempt in range(SEND_RETRIES):
            try:
                sms = await services.sms.send(f.phone_e164, body, idempotency_key=key)
            except SmsError as exc:
                last_error = str(exc)
                if not exc.retryable or attempt == SEND_RETRIES - 1:
                    break
                await asyncio.sleep(0.2 * 2**attempt)
                continue
            await store.mark_message(
                state,
                claim["id"],
                provider_sid=sms.provider_sid,
                status=sms.status,
                sent_at=now,
                attempts=attempt + 1,
            )
            await store.add_contact(state, claim["id"], now)
            await store.audit(
                state,
                "sms.sent",
                "message",
                claim["id"],
                {"draft_version": version, "approval": approval["decision"]},
            )
            await set_status(state, "sent")
            return {
                "send": {
                    "status": "sent",
                    "provider_sid": sms.provider_sid,
                    "message_id": claim["id"],
                }
            }
        await store.mark_message(
            state, claim["id"], status="failed", error=last_error[:500], attempts=SEND_RETRIES
        )
        await store.follow_up(
            state, "send_retry", now + timedelta(hours=1), {"error": last_error[:200]}
        )
        await store.audit(state, "sms.failed", "message", claim["id"], {"error": last_error[:200]})
        await set_status(state, "failed")
        return {
            "send": {"status": "failed", "error": last_error[:200]},
            "closed_reason": "send_failed",
        }

    async def schedule_follow_up(state: CaseState) -> dict[str, Any]:
        await store.follow_up(
            state,
            "after_send",
            business_days_after(now_of(state), 3),
            {"message_id": state["send"].get("message_id")},
        )
        return {}

    async def await_reply(state: CaseState) -> dict[str, Any]:
        await set_status(state, "awaiting_reply")
        event = interrupt({"kind": "await_reply"})
        reason = event.get("event", "reply") if isinstance(event, dict) else "reply"
        return {"closed_reason": str(reason)}

    async def close(state: CaseState) -> dict[str, Any]:
        reason = state.get("closed_reason") or (state.get("nba") or {}).get("action") or "closed"
        if (state.get("nba") or {}).get("requires_analyst"):
            return {"status": "requires_analyst"}
        precheck = state.get("precheck") or {}
        if not precheck.get("ok", True):
            reason = "precheck_blocked"
        final = "failed" if reason in ("send_failed",) else "closed"
        await set_status(state, final, closed_at=services.clock())
        await store.audit(state, "case.closed", "case", state["case_id"], {"reason": reason})
        return {"status": final, "closed_reason": reason}

    # --- routing ----------------------------------------------------------------------------
    def after_precheck(state: CaseState) -> str:
        return "next_best_action" if state["precheck"].get("ok") else "close"

    def after_nba(state: CaseState) -> str:
        nba = state["nba"]
        if nba.get("requires_analyst"):
            return "close"
        return {"pause": "close", "no_action": "close", "escalate": "notify_supervisor"}.get(
            nba["action"], "draft"
        )

    def after_postcheck(state: CaseState) -> str:
        if state["postcheck"].get("ok") or state.get("requires_edit"):
            return "human_approval"
        return "draft"

    def after_approval(state: CaseState) -> str:
        decision = state["approval"].get("decision")
        if decision in ("reject", "cancelled"):
            return "close"
        if decision == "invalid":
            return "human_approval"
        return "wait_send_window"

    def after_send(state: CaseState) -> str:
        status = state["send"].get("status")
        if status == "deferred":
            return "wait_send_window"
        return "schedule_follow_up" if status == "sent" else "close"

    g: StateGraph[CaseState] = StateGraph(CaseState)
    for name, fn in [
        ("load_account", load_account),
        ("prioritize", prioritize),
        ("compliance_precheck", compliance_precheck),
        ("next_best_action", next_best_action),
        ("notify_supervisor", notify_supervisor),
        ("draft", draft),
        ("compliance_postcheck", compliance_postcheck),
        ("human_approval", human_approval),
        ("wait_send_window", wait_send_window),
        ("send_sms", send_sms),
        ("schedule_follow_up", schedule_follow_up),
        ("await_reply", await_reply),
        ("close", close),
    ]:
        g.add_node(name, node(name, fn))  # type: ignore[call-overload]
    g.add_edge(START, "load_account")
    g.add_edge("load_account", "prioritize")
    g.add_edge("prioritize", "compliance_precheck")
    g.add_conditional_edges("compliance_precheck", after_precheck, ["next_best_action", "close"])
    g.add_conditional_edges("next_best_action", after_nba, ["close", "notify_supervisor", "draft"])
    g.add_edge("notify_supervisor", "close")
    g.add_edge("draft", "compliance_postcheck")
    g.add_conditional_edges("compliance_postcheck", after_postcheck, ["human_approval", "draft"])
    g.add_conditional_edges(
        "human_approval", after_approval, ["close", "human_approval", "wait_send_window"]
    )
    g.add_edge("wait_send_window", "send_sms")
    g.add_conditional_edges(
        "send_sms", after_send, ["wait_send_window", "schedule_follow_up", "close"]
    )
    g.add_edge("schedule_follow_up", "await_reply")
    g.add_edge("await_reply", "close")
    g.add_edge("close", END)
    return g.compile(checkpointer=checkpointer)


def _is_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def eval_graph() -> Any:
    """evalgate target (`langgraph: collections_copilot.agent.case_graph:eval_graph`): in-memory
    store, in-memory checkpointer and the configured provider (FakeLLM unless CC_LLM_PROVIDER is set)."""
    from langgraph.checkpoint.memory import InMemorySaver

    from collections_copilot.agent.store import MemoryCaseStore
    from collections_copilot.config import get_settings
    from collections_copilot.llm.gateway import LLMGateway
    from collections_copilot.llm.providers import build_provider

    settings = get_settings()
    services = Services(
        settings=settings,
        store=MemoryCaseStore(),
        gateway=LLMGateway.from_settings(settings, build_provider(settings)),
    )
    return build_case_graph(services, InMemorySaver())

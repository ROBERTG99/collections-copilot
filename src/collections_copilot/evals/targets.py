"""evalgate targets and spies (BUILD.md §11).

- `classify_reply`: the reply graph's own decision code (STOP rule, `classify_text`,
  `promise_problems`) on one reply, with a spy provider and a spy ledger that records a write-back
  exactly when the graph would call `record_promise`.
- `compliance_case`: the full case graph (in-memory store and checkpointer) driven by a careless
  approver that approves or edits in whatever the row gives it, bypassing the approval API's own
  checks on purpose, and a scheduler that resumes deferred sends at their send time. Every SMS
  the graph sends, every ledger call and every provider request is recorded.

- `nba_case`: `collections_copilot.agent.case_graph:eval_graph` on one account, streamed node by
  node (one step record per node, as evalgate's `langgraph:` target does; that target streams a
  compiled graph without a `thread_id`, which the graph's checkpointer needs).
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from evalgate.contracts import StepRecord as EvalStep
from evalgate.contracts import TargetResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from collections_copilot.agent.case_graph import build_case_graph, eval_graph
from collections_copilot.agent.reply_graph import classify_text, may_write_back, promise_problems
from collections_copilot.agent.services import Services
from collections_copilot.agent.store import MemoryCaseStore
from collections_copilot.compliance.rules.footer import has_footer
from collections_copilot.compliance.stop_keywords import is_stop
from collections_copilot.config import get_settings
from collections_copilot.integrations.mcp.ledger import LedgerResult
from collections_copilot.integrations.sms.base import InMemorySmsSender, SmsResult
from collections_copilot.llm.gateway import LLMGateway
from collections_copilot.llm.providers import build_provider
from collections_copilot.llm.providers.base import Provider, ProviderRequest, ProviderResponse
from collections_copilot.llm.redaction import LEGAL_SUFFIXES
from collections_copilot.observability.steps import InMemoryStepSink

MAX_RESUMES = 8
_JOIN_DIGITS = re.compile(r"(?<=\d)[\s().-](?=\d)")


def pii_terms(name: str, phone: str, extra: list[str]) -> list[str]:
    terms = [t for t in (name, LEGAL_SUFFIXES.sub("", name).strip(), phone, *extra) if t]
    return list(dict.fromkeys(terms))


class SpyProvider:
    """Wraps the configured provider and records any listed personal data it is sent."""

    def __init__(self, inner: Provider, terms: list[str]) -> None:
        self.inner = inner
        self.name = f"spy:{inner.name}"
        self.terms = terms
        self.requests: list[ProviderRequest] = []
        self.leaks: list[str] = []

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        text = f"{request.system}\n{request.user}"
        low = text.lower()
        joined = _JOIN_DIGITS.sub("", text)
        for term in self.terms:
            digits = re.sub(r"\D", "", term)
            phone_like = len(digits) >= 8 and len(digits) >= len(term) * 0.6
            if term.lower() in low or (phone_like and digits in joined):
                self.leaks.append(term)
        return await self.inner.complete(request)


@dataclass
class SpyLedger:
    """Upstream spy: every write-back attempt is recorded and accepted."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    async def record_promise_to_pay(
        self,
        tenant: str,
        args: dict[str, Any],
        *,
        idempotency_key: str,
        approval_id: str | None = None,
    ) -> LedgerResult:
        self.calls.append({"tenant": tenant, "args": args, "key": idempotency_key})
        return LedgerResult(status="recorded", ledger_ref=f"spy-{len(self.calls)}")

    async def get_balance(self, tenant: str, account_ref: str) -> dict[str, Any]:
        return {}


@dataclass
class ClockedSms(InMemorySmsSender):
    """Records each send with the eval clock's time."""

    clock: Any = None
    times: list[datetime] = field(default_factory=list)

    async def send(self, to_e164: str, body: str, *, idempotency_key: str) -> SmsResult:
        result = await super().send(to_e164, body, idempotency_key=idempotency_key)
        self.times.append(self.clock())
        return result


def _steps(sink: InMemoryStepSink) -> list[EvalStep]:
    return [
        EvalStep(
            node=r.node,
            kind=r.kind,
            model=r.model,
            prompt_version=r.prompt_version,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            cost_usd=r.cost_usd,
            latency_ms=r.latency_ms,
            # evalgate's step statuses are ok, error, timeout and skipped: a model call that
            # did not succeed is an error, with the gateway's own status kept.
            status="ok" if r.status == "ok" else "error",
            error=None if r.status == "ok" else r.status,
        )
        for r in sink.records
        if r.kind == "llm"
    ]


async def nba_case(case_input: dict[str, Any]) -> TargetResult:
    graph = eval_graph()
    config: Any = {"configurable": {"thread_id": str(uuid.uuid4())}}
    state: dict[str, Any] = dict(case_input)
    steps: list[EvalStep] = []
    previous = time.perf_counter()
    async for update in graph.astream(case_input, config, stream_mode="updates"):
        now = time.perf_counter()
        for node, delta in update.items():
            if node == "__interrupt__":
                state["__interrupt__"] = True
                continue
            steps.append(
                EvalStep(
                    node=node, step=len(steps), kind="other", latency_ms=(now - previous) * 1000
                )
            )
            if isinstance(delta, dict):
                state.update(delta)
        previous = now
    return TargetResult(output=state, steps=steps)


async def classify_reply(case_input: dict[str, Any]) -> TargetResult:
    started = time.perf_counter()
    settings = get_settings()
    acc = case_input["account"]
    spy = SpyProvider(
        build_provider(settings),
        pii_terms(acc["name"], acc["phone_e164"], case_input.get("pii_terms", [])),
    )
    sink = InMemoryStepSink()
    services = Services(
        settings=settings,
        store=MemoryCaseStore(),
        gateway=LLMGateway.from_settings(settings, spy, sink=sink),
    )
    ledger = SpyLedger()
    today = date.fromisoformat(case_input["today"])
    body = case_input["body"]
    if is_stop(body):  # FR-8: the rule runs first and STOP never reaches the model
        label, confidence, promise, source = "stop", 1.0, None, "rule"
    else:
        label, confidence, promise, source = await classify_text(
            services,
            message_id=case_input["message_id"],
            body=body,
            account=acc,
            today=today,
        )
    problems = promise_problems(promise, int(acc["open_balance_cents"]), today, body=body)
    if may_write_back(label, problems):  # the reply graph's own route to record_promise
        await ledger.record_promise_to_pay(
            "eval",
            {"account_ref": acc["external_ref"], **(promise or {})},
            idempotency_key=f"ptp:eval:{case_input['message_id']}",
        )
    steps = _steps(sink)
    return TargetResult(
        output={
            "label": label,
            "confidence": confidence,
            "promise": promise,
            "source": source,
            "write_back": bool(ledger.calls),
            "problems": problems if label == "promise_to_pay" else [],
            "llm_calls": len(spy.requests),
            "pii_leaks": spy.leaks,
        },
        steps=steps,
        cost_usd=sum(s.cost_usd for s in steps),
        latency_ms=(time.perf_counter() - started) * 1000,
    )


async def compliance_case(case_input: dict[str, Any]) -> TargetResult:
    started = time.perf_counter()
    settings = get_settings()
    facts = case_input["input_facts"]
    clock = {"now": datetime.fromisoformat(case_input["now"])}
    spy = SpyProvider(
        build_provider(settings),
        pii_terms(facts["name"], facts["phone_e164"], case_input.get("pii_terms", [])),
    )
    sink = InMemoryStepSink()
    store = MemoryCaseStore()
    sms = ClockedSms(clock=lambda: clock["now"])
    ledger = SpyLedger()
    services = Services(
        settings=settings,
        store=store,
        gateway=LLMGateway.from_settings(settings, spy, sink=sink),
        sms=sms,
        clock=lambda: clock["now"],
        ledger=ledger,
    )
    graph = build_case_graph(services, InMemorySaver())
    case_id = case_input["case_id"]
    config: Any = {"configurable": {"thread_id": case_id}}
    approver = case_input.get("approver") or {"kind": "none"}
    ref = {"case_id": case_id}
    payload: Any = {
        k: case_input[k] for k in ("case_id", "tenant_id", "run_id", "now", "input_facts")
    }
    pauses: list[str] = []
    exhausted = True
    for _ in range(MAX_RESUMES):
        await graph.ainvoke(payload, config)
        snap = await graph.aget_state(config)
        if not snap.interrupts:
            exhausted = False
            break
        value = snap.interrupts[0].value
        kind = value.get("kind") if isinstance(value, dict) else None
        pauses.append(str(kind))
        if kind == "approval" and approver["kind"] in ("approve", "edit"):
            latest = await store.latest_draft(ref)
            assert latest is not None
            version = int(latest["version"])
            if approver["kind"] == "edit":  # what the approval API stores for an edit
                version += 1
                await store.save_draft(
                    ref,
                    version=version,
                    body=approver["body"],
                    author="human",
                    language=facts["language"],
                    model=None,
                    prompt_version=None,
                    precheck=None,
                    postcheck=None,
                )
            store.approve(case_id, version, approver["kind"], actor="eval-approver")
            payload = Command(
                resume={
                    "decision": approver["kind"],
                    "body": approver.get("body"),
                    "actor": "eval-approver",
                }
            )
        elif kind == "deferred_send":  # the scheduler resumes the case at its send time
            clock["now"] = datetime.fromisoformat(value["send_at"])
            payload = Command(resume={"kind": "due"}, update={"now": value["send_at"]})
        else:  # waiting for a human who never comes, or for the debtor's reply
            exhausted = False
            break
    final = (await graph.aget_state(config)).values
    tz = ZoneInfo(facts["timezone"])
    sent = []
    for msg, at in zip(sms.sent, sms.times, strict=True):
        # The send's own draft version (idempotency key `sms:{case_id}:{version}`) must carry
        # an approval: an approval of another version with the same text does not count.
        version = int(msg["key"].rsplit(":", 1)[-1])
        approved = (case_id, version) in store.approvals and store.drafts.get(
            (case_id, version), {}
        ).get("body") == msg["body"]
        sent.append(
            {
                "body": msg["body"],
                "at": at.isoformat(),
                "local_hour": at.astimezone(tz).hour,
                "approved": approved,
                "footer": has_footer(msg["body"], facts["language"]),
            }
        )
    steps = _steps(sink)
    return TargetResult(
        output={
            "sent": sent,
            "writebacks": len(ledger.calls),
            "pii_leaks": spy.leaks,
            "pauses": pauses,
            "status": final.get("status"),
            "closed_reason": final.get("closed_reason"),
            "nba": final.get("nba"),
            "send": final.get("send"),
            "resumes_exhausted": exhausted,  # MAX_RESUMES ran out before the case settled
        },
        steps=steps,
        cost_usd=sum(s.cost_usd for s in steps),
        latency_ms=(time.perf_counter() - started) * 1000,
    )

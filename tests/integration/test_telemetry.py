"""T-42 (§14): one span per graph node, LLM spans with gen_ai attributes, metrics and propagation."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import REGISTRY

from collections_copilot.agent.case_graph import build_case_graph
from collections_copilot.agent.services import Services
from collections_copilot.agent.store import MemoryCaseStore
from collections_copilot.config import Settings
from collections_copilot.llm.gateway import LLMGateway
from collections_copilot.llm.providers.fake import FakeLLM
from collections_copilot.observability import tracing
from collections_copilot.observability.steps import InMemoryStepSink

pytestmark = pytest.mark.integration

DATASET = Path("evals/datasets/nba.v1.jsonl")


@pytest.fixture
def spans() -> InMemorySpanExporter:
    exporter = tracing.install_test_exporter()
    exporter.clear()
    return exporter


def _remind_case() -> dict[str, Any]:
    for line in DATASET.read_text().splitlines():
        row = json.loads(line)
        if row["split"] == "dev" and row["expected"]["action"] == "remind":
            return dict(row["input"])
    raise AssertionError("no remind row in the dev split")


def _sample(name: str, labels: dict[str, str]) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_span_per_node_with_gen_ai_attributes(spans: InMemorySpanExporter) -> None:
    sink = InMemoryStepSink()
    services = Services(
        settings=Settings(),
        store=MemoryCaseStore(),
        gateway=LLMGateway(FakeLLM(), sink=sink),
    )
    graph = build_case_graph(services, InMemorySaver())
    case = _remind_case()
    tokens_before = _sample(
        "cc_llm_tokens_total",
        {
            "route": "next_best_action",
            "model": "fake",
            "direction": "input",
        },
    )
    await graph.ainvoke(case, {"configurable": {"thread_id": str(uuid.uuid4())}})

    finished = spans.get_finished_spans()
    node_spans = {s.attributes["cc.node"]: s for s in finished if s.name.startswith("node ")}
    executed = {r.node for r in sink.records if r.kind == "node"}
    assert executed  # the graph ran
    assert set(node_spans) == executed  # exactly one span per executed node
    for name, span in node_spans.items():
        assert span.attributes["cc.case_id"] == case["case_id"]
        assert span.attributes["cc.tenant_id"] == case["tenant_id"]
        assert span.name == f"node {name}"

    llm = [
        s for s in finished if s.attributes and s.attributes.get("gen_ai.operation.name") == "chat"
    ]
    assert llm, "no LLM span"
    nba = next(s for s in llm if s.attributes["cc.route"] == "next_best_action")
    attrs = nba.attributes
    assert attrs["gen_ai.provider.name"] == "fake"
    assert attrs["gen_ai.request.model"] == services.routes["next_best_action"].model
    assert attrs["gen_ai.usage.input_tokens"] > 0
    assert attrs["gen_ai.usage.output_tokens"] > 0
    assert attrs["cc.prompt_version"] == "next_best_action.v1"
    assert len(attrs["cc.prompt_hash"]) == 64
    assert attrs["cc.status"] == "ok"
    # The LLM span is a child of its node's span, so a trace reads node by node.
    parent = node_spans["next_best_action"]
    assert nba.parent is not None
    assert nba.parent.span_id == parent.context.span_id
    # No message text or phone number ever lands on a span.
    for span in finished:
        for value in (span.attributes or {}).values():
            assert "+1555" not in str(value)
            assert "+34555" not in str(value)

    # Prometheus: node durations and token counters move with the run.
    assert (
        _sample(
            "cc_node_duration_seconds_count",
            {"graph": "case", "node": "next_best_action", "status": "ok"},
        )
        >= 1
    )
    assert (
        _sample(
            "cc_llm_tokens_total",
            {
                "route": "next_best_action",
                "model": "fake",
                "direction": "input",
            },
        )
        > tokens_before
    )
    assert _sample("cc_nba_decisions_total", {"action": "remind"}) >= 1


async def test_queue_messages_carry_the_trace(spans: InMemorySpanExporter) -> None:
    carrier: dict[str, str] = {}
    with tracing.tracer().start_as_current_span("enqueue") as producer:
        tracing.inject(carrier)
    assert "traceparent" in carrier
    with tracing.consumer_span("process cc-tasks", carrier) as consumer:
        assert consumer.get_span_context().trace_id == producer.get_span_context().trace_id

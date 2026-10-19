"""Prometheus metrics (§14). Labels are low-cardinality: never tenant, case, phone or text.

With several API worker processes (`CC_API_WORKERS`), `PROMETHEUS_MULTIPROC_DIR` is set before
they start: each process writes its values there and `scrape_registry` sums them per scrape.
"""

from __future__ import annotations

import os

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge, Histogram, multiprocess

HTTP_DURATION = Histogram(
    "cc_http_request_duration_seconds",
    "API request duration by route template and status.",
    ["method", "route", "status"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1, 2.5, 5, 10),
)
NODE_DURATION = Histogram(
    "cc_node_duration_seconds",
    "Graph node duration (an interrupted node counts until it paused).",
    ["graph", "node", "status"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
LLM_TOKENS = Counter(
    "cc_llm_tokens_total",
    "Model tokens by route, model and direction.",
    ["route", "model", "direction"],
)
LLM_COST = Counter(
    "cc_llm_cost_usd_total", "Model cost in USD by route and model.", ["route", "model"]
)
NBA_DECISIONS = Counter("cc_nba_decisions_total", "Next-best-action decisions.", ["action"])
APPROVALS = Counter("cc_approvals_total", "Human approval decisions.", ["decision"])
SMS_SENT = Counter("cc_sms_sent_total", "Outbound SMS attempts by result.", ["status"])
DLQ_DEPTH = Gauge("cc_sqs_dlq_depth", "Messages in each dead-letter queue.", ["queue"])


def scrape_registry() -> CollectorRegistry:
    """What /metrics serves: this process's registry, or every worker's values in multiprocess mode."""
    if not os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        return REGISTRY
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
    return registry

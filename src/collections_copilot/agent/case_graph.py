"""Case graph (walking skeleton): load_account → … → human_approval interrupt.

The skeleton nodes are stubs; T-22 replaces them with the full §4.5 graph.
"""

from __future__ import annotations

import json
from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from collections_copilot.llm.providers.base import ProviderRequest
from collections_copilot.llm.providers.fake import FakeLLM


class CaseState(TypedDict, total=False):
    case_id: str
    tenant_id: str
    account_id: str
    status: str
    draft: dict[str, Any]
    approval: dict[str, Any]


def build_skeleton_graph(
    checkpointer: BaseCheckpointSaver[Any] | None, llm: FakeLLM | None = None
) -> Any:
    model = llm or FakeLLM()

    async def load_account(state: CaseState) -> CaseState:
        return {"status": "processing"}

    async def draft(state: CaseState) -> CaseState:
        response = await model.complete(
            ProviderRequest(
                "draft_message", "fake", "skeleton", "<context>{}</context>", {}, "low", 256
            )
        )
        return {"draft": json.loads(response.text)}

    async def human_approval(state: CaseState) -> CaseState:
        decision = interrupt({"kind": "approval", "draft": state.get("draft")})
        return {"approval": decision, "status": "closed"}

    g: StateGraph[CaseState] = StateGraph(CaseState)
    g.add_node("load_account", load_account)
    g.add_node("draft", draft)
    g.add_node("human_approval", human_approval)
    g.add_edge(START, "load_account")
    g.add_edge("load_account", "draft")
    g.add_edge("draft", "human_approval")
    g.add_edge("human_approval", END)
    return g.compile(checkpointer=checkpointer)

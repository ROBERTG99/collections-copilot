"""prioritize rationale (FR-2): the model explains a deterministic score and never changes it.
On any failure the score is kept and the rationale is None."""

from __future__ import annotations

from collections_copilot.agent.prompts import render
from collections_copilot.agent.schemas import PriorityRationale
from collections_copilot.llm.gateway import LLMGateway, RouteSpec
from collections_copilot.scoring.priority import PriorityScore


async def priority_rationale(
    gateway: LLMGateway, routes: dict[str, RouteSpec], score: PriorityScore
) -> str | None:
    rendered = render(
        "prioritize_rationale",
        {"score": score.score, "components": score.components, "formula": score.version},
        [],
    )
    result = await gateway.structured(
        routes["prioritize_rationale"],
        system=rendered.system,
        user=rendered.user,
        schema=PriorityRationale,
        prompt_version=f"{rendered.name}.{rendered.version}",
        prompt_hash=rendered.hash,
    )
    return result.parsed.rationale if result.ok and result.parsed is not None else None

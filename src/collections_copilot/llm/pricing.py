"""Token cost accounting from llm/pricing.yaml."""

from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml

from collections_copilot.llm.providers.base import Usage

PRICING_FILE = Path(__file__).with_name("pricing.yaml")


@cache
def table() -> dict[str, dict[str, float]]:
    data = yaml.safe_load(PRICING_FILE.read_text())
    return {k: {kk: float(vv) for kk, vv in v.items()} for k, v in data["models"].items()}


def cost_usd(model: str, usage: Usage) -> float:
    prices = table().get(model)
    if prices is None:
        prices = table().get(model.removeprefix("anthropic."), table()["fake"])
    return (
        usage.input_tokens * prices["input"]
        + usage.output_tokens * prices["output"]
        + usage.cache_read_input_tokens * prices["cache_read"]
        + usage.cache_creation_input_tokens * prices["cache_write"]
    ) / 1_000_000


def estimate_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    return cost_usd(model, Usage(input_tokens=input_tokens, output_tokens=output_tokens))

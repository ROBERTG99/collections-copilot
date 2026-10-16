"""Deterministic FakeLLM: canned, schema-valid outputs per route (offline default)."""

from __future__ import annotations

import json
from typing import Any


class FakeLLM:
    name = "fake"

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[dict[str, Any]] = []

    async def complete(self, route: str, system: str, user: str) -> str:
        self.calls.append({"route": route, "system": system, "user": user})
        if route in self.responses:
            value = self.responses[route]
            return value if isinstance(value, str) else json.dumps(value)
        if route == "draft_message":
            return json.dumps({"body": "Hello, this is a reminder about your open balance."})
        return json.dumps({})

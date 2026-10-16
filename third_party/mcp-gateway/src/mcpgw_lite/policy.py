"""YAML policy (fallback for the upstream OPA bundle): allow, deny or require approval."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import yaml


@dataclass(frozen=True)
class Decision:
    effect: Literal["allow", "deny", "require_approval"]
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ToolPolicy:
    scope: str
    write: bool


class Policy:
    def __init__(self, doc: dict[str, Any]) -> None:
        self.tools: dict[str, ToolPolicy] = {
            name: ToolPolicy(scope=spec["scope"], write=bool(spec.get("write", False)))
            for name, spec in (doc.get("tools") or {}).items()
        }
        self.agents: dict[str, set[str]] = {
            agent: set(spec.get("tools", [])) for agent, spec in (doc.get("agents") or {}).items()
        }
        self.approval_rules: dict[str, list[dict[str, Any]]] = doc.get("approval_rules") or {}

    @classmethod
    def load(cls, path: str | Path) -> Policy:
        return cls(yaml.safe_load(Path(path).read_text()))

    def evaluate(
        self,
        agent: str,
        scopes: frozenset[str],
        tool: str,
        args: dict[str, Any],
        today: date | None = None,
    ) -> Decision:
        tp = self.tools.get(tool)
        if tp is None:
            return Decision("deny", ["tool_not_in_policy"])
        if tool not in self.agents.get(agent, set()):
            return Decision("deny", ["agent_not_allowed_tool"])
        if tp.scope not in scopes:
            return Decision("deny", [f"missing_scope:{tp.scope}"])
        reasons = []
        today = today or datetime.now(UTC).date()
        for rule in self.approval_rules.get(tool, []):
            value = args.get(rule["field"])
            if value is None:
                continue
            op, limit = rule["op"], rule["value"]
            if op == "gt" and float(value) > float(limit):
                reasons.append(rule["reason"])
            elif op == "days_from_today_gt":
                try:
                    days = (date.fromisoformat(str(value)) - today).days
                except ValueError:
                    return Decision("deny", [f"invalid_date:{rule['field']}"])
                if days > int(limit):
                    reasons.append(rule["reason"])
        return Decision("require_approval", reasons) if reasons else Decision("allow")

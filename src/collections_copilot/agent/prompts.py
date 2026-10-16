"""Versioned prompt files (`prompts/<name>.v<N>.md`), rendering and hashing.

The system prompt holds the stable instructions (and the tone policy where included): it is the
cached prefix and never contains case data. The user turn carries a `<context>` JSON block and
then the untrusted notes or replies, each fenced in an `<untrusted_note>` block whose tags cannot
be forged from inside.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"
_TAG = re.compile(r"</?\s*untrusted_note[^>]*>", re.I)
_INCLUDE = re.compile(r"\{\{([\w/.-]+)\}\}")


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    route: str
    schema_name: str
    system: str
    sha256: str

    @property
    def id(self) -> str:
        return f"{self.name}.{self.version}"


@dataclass(frozen=True)
class UntrustedNote:
    id: str
    source: str
    body: str
    created_at: str = ""


@dataclass(frozen=True)
class RenderedPrompt:
    name: str
    version: str
    hash: str
    system: str
    user: str
    schema_name: str


def prompts_dir() -> Path:
    return PROMPTS_DIR


def prompt_names() -> list[str]:
    return sorted({p.name.split(".")[0] for p in prompts_dir().glob("*.v*.md")})


@cache
def load_prompt(name: str, version: str = "v1") -> Prompt:
    path = prompts_dir() / f"{name}.{version}.md"
    raw = path.read_text()
    _, front, body = raw.split("---\n", 2)
    meta = yaml.safe_load(front)

    def include(m: re.Match[str]) -> str:
        return (prompts_dir() / m.group(1)).read_text().strip()

    system = _INCLUDE.sub(include, body).strip()
    digest = hashlib.sha256(system.encode()).hexdigest()
    return Prompt(meta["name"], meta["version"], meta["route"], meta["schema"], system, digest)


def _fence(note: UntrustedNote) -> str:
    body = _TAG.sub("[tag removed]", note.body)
    body = "".join(ch for ch in body if ch in "\n\t" or ch.isprintable())
    body = body.replace("<", "&lt;").replace(">", "&gt;")
    attrs = f'id="{_attr(note.id)}" source="{_attr(note.source)}"'
    if note.created_at:
        attrs += f' created_at="{_attr(note.created_at)}"'
    return f"<untrusted_note {attrs}>\n{body}\n</untrusted_note>"


def _attr(value: str) -> str:
    return re.sub(r"[^\w:.@+-]", "", value)[:64]


def render(
    name: str, context: dict[str, Any], notes: list[UntrustedNote], version: str = "v1"
) -> RenderedPrompt:
    prompt = load_prompt(name, version)
    parts = [
        "<context>",
        json.dumps(context, ensure_ascii=False, sort_keys=True, default=str),
        "</context>",
    ]
    parts += [_fence(n) for n in notes]
    return RenderedPrompt(
        prompt.name,
        prompt.version,
        prompt.sha256,
        prompt.system,
        "\n".join(parts),
        prompt.schema_name,
    )


def template(name: str, language: str) -> str:
    lang = language if language in ("en", "es") else "en"
    return (prompts_dir() / "templates" / f"{name}.{lang}.txt").read_text().strip()


async def render_for_spy(provider: Any, redactor: Any, account: Any) -> None:
    """Test hook for the PII spy: push one account's NBA and draft prompts through the gateway."""
    from collections_copilot.agent.schemas import Draft, NextBestAction
    from collections_copilot.llm.gateway import LLMGateway, RouteSpec
    from collections_copilot.llm.redaction import AccountPII

    gateway = LLMGateway(provider, redactor=redactor)
    pii = AccountPII(name=account.name, phone_e164=account.phone_e164)
    notes = [
        UntrustedNote(id=f"n{i}", source=n.source, body=n.body) for i, n in enumerate(account.notes)
    ]
    notes.append(
        UntrustedNote(
            id="probe", source="crm", body=f"Spoke to {account.name} at {account.phone_e164}."
        )
    )
    context = {
        "account_name": account.name,
        "phone": account.phone_e164,
        "segment": account.segment,
    }
    for route, schema in (("next_best_action", NextBestAction), ("draft_message", Draft)):
        rendered = render(route, context, notes)
        await gateway.structured(
            RouteSpec(route, "fake", "low", 256),
            system=rendered.system,
            user=rendered.user,
            schema=schema,
            prompt_version=f"{rendered.name}.{rendered.version}",
            prompt_hash=rendered.hash,
            pii=pii,
        )

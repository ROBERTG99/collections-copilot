"""Links are allowed only to hosts on the allowlist."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

ALLOWED_HOSTS = frozenset({"pay.collections.example"})
URL = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)
BARE_DOMAIN = re.compile(
    r"\b(?:[a-z0-9-]+\.)+(?:com|net|org|io|co|es|biz|info|xyz|link|ly|me)\b(?:/\S*)?", re.I
)


def _host(url: str) -> str:
    target = url if "://" in url else f"http://{url}"
    return (urlsplit(target.rstrip(".,;:!?)")).hostname or "").lower()


def find_disallowed(text: str, allowed: frozenset[str] = ALLOWED_HOSTS) -> list[str]:
    hosts = [_host(m.group(0)) for m in URL.finditer(text)]
    stripped = URL.sub(" ", text)
    hosts += [_host(m.group(0)) for m in BARE_DOMAIN.finditer(stripped)]
    return [h for h in hosts if h and h not in allowed]

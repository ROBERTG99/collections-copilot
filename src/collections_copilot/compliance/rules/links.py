"""Links are allowed only to hosts on the allowlist (fail closed).

Any `label(.label)+` with an alphabetic top-level label of two or more letters counts as a host,
with or without a scheme, so shorteners (goo.gl), new TLDs (evil.app) and look-alikes
(pay.collections.example.evil.ru) are all flagged.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

ALLOWED_HOSTS = frozenset({"pay.collections.example"})
URL = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)
HOST = re.compile(
    r"(?<![\w@.-])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63})(?![\w-])(?:/\S*)?",
    re.IGNORECASE,
)


def _host(url: str) -> str:
    target = url if "://" in url else f"http://{url}"
    return (urlsplit(target.rstrip(".,;:!?)")).hostname or "").lower()


def find_disallowed(text: str, allowed: frozenset[str] = ALLOWED_HOSTS) -> list[str]:
    hosts = [_host(m.group(0)) for m in URL.finditer(text)]
    stripped = URL.sub(" ", text)
    hosts += [m.group(1).lower() for m in HOST.finditer(stripped)]
    return [h for h in hosts if h and h not in allowed]

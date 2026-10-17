"""Licence check for `make security`: no strong-copyleft licence in shipped dependencies.

Python: every distribution in the project environment (License-Expression, License, classifiers).
Web: `pnpm licenses list --prod --json` from `web/`. LGPL is allowed (dynamically linked, e.g.
sharp's libvips); GPL, AGPL, SSPL and "Commons Clause" fail. Unknown licences are listed.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from importlib import metadata

DENY = re.compile(r"(?<![L])\b(A?GPL|GNU (?:Affero )?General Public)|SSPL|Commons Clause", re.I)
LESSER = re.compile(r"LGPL|Lesser General Public", re.I)
OWN = {"collections-copilot", "dev-oidc", "ledger-mcp", "debtor-sim", "evalgate", "mcpgw-lite"}


def _python() -> dict[str, str]:
    found: dict[str, str] = {}
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if not name or name.lower() in OWN:
            continue
        parts = [dist.metadata.get("License-Expression") or "", dist.metadata.get("License") or ""]
        parts += [
            c for c in dist.metadata.get_all("Classifier") or [] if c.startswith("License ::")
        ]
        found[name] = " | ".join(p.strip().splitlines()[0] for p in parts if p.strip())
    return found


def _web() -> dict[str, str]:
    out = subprocess.run(
        ["pnpm", "licenses", "list", "--prod", "--json"],
        cwd="web",
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {pkg["name"]: lic for lic, pkgs in json.loads(out).items() for pkg in pkgs}


def denied(licence: str) -> bool:
    return bool(DENY.search(LESSER.sub("", licence)))


def main() -> int:
    bad, unknown = [], []
    for ecosystem, deps in (("python", _python()), ("web", _web())):
        for name, licence in sorted(deps.items()):
            if not licence or licence.upper() == "UNKNOWN":
                unknown.append(f"{ecosystem}:{name}")
            elif denied(licence):
                bad.append(f"{ecosystem}:{name}: {licence}")
    if unknown:
        print(f"licences not declared in metadata ({len(unknown)}): {', '.join(unknown)}")
    for line in bad:
        print(f"DENIED {line}")
    print("licences: FAIL" if bad else "licences: no strong-copyleft dependency")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

"""Write the API's OpenAPI document (no running stack needed): `make gen-types` input."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from collections_copilot.api.main import create_app
from collections_copilot.config import Settings


def main(out: str) -> None:
    app = create_app(Settings(env="test"))
    schema = app.openapi()
    Path(out).write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out}: {len(schema['paths'])} paths")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "docs/openapi.json")

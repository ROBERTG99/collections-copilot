---
name: tdd
description: "Test-driven loop for a change: failing test first, implement, verify, commit."
---
# TDD loop

1. Read the code the change touches, its ADRs and `docs/KNOWN_LIMITATIONS.md`.
2. Write the failing test; run it and confirm it fails for the right reason.
3. Implement the smallest change that makes it pass; keep tenant scoping, cents and async conventions.
4. Run the area checks: `make check-fast`, plus `uv run pytest <integration path>` when the change touches the database, queues or HTTP.
5. Record any design choice in `docs/DECISIONS.md` and any gap in `docs/KNOWN_LIMITATIONS.md`.
6. Commit with a Conventional Commit message and no attribution trailers.

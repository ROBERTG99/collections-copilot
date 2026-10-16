---
name: tdd
description: "Test-driven loop for a BUILD.md task: failing test first, implement, verify, record evidence, commit."
---
# TDD loop

1. Read the task in `docs/specs/000-build-spec.md` §8 and its row in `PROGRESS.md`.
2. Write the named test ("Test first"); run it and confirm it fails for the right reason.
3. Implement the smallest change that makes it pass; keep tenant scoping, cents and async conventions.
4. Run the area checks: `make check-fast`, plus `uv run pytest <integration path>` when the task touches the database, queues or HTTP.
5. Update `PROGRESS.md` with `python scripts/progress.py T-NN done "<command> → <one-line result>"`.
6. Commit with a Conventional Commit message and no attribution trailers.

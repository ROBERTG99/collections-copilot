---
paths:
  - "tests/**"
  - "web/tests/**"
---
# Testing rules

- Write the failing test first and run it to see it fail for the right reason.
- No sleeps: poll with a timeout (`tests/helpers.py::eventually`). Freeze clocks with `time-machine`. Fixed seeds.
- One tenant per integration test (`tenant` fixture creates a fresh one).
- Never skip, xfail or weaken a test or threshold to get green; fix the code or record the gap in `docs/KNOWN_LIMITATIONS.md`.
- Unit tests need no network; integration tests use the compose dependencies (`make deps-up`); live LLM calls are never made in tests.

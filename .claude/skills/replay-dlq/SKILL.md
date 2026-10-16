---
name: replay-dlq
description: "Inspect and replay messages from a dead-letter queue."
---
# Replay a DLQ

1. Inspect: `uv run cc dlq peek --queue cc-tasks-dlq --max 10` (bodies are redacted).
2. Fix the root cause first; replaying a poison message only refills the DLQ.
3. Replay: `uv run cc replay-dlq --queue cc-tasks-dlq --max 10` (moves messages back to the source queue, preserving `task_id` so handlers stay idempotent).
4. Watch `cc_sqs_dlq_depth` return to 0; alert `CCDLQNotEmpty` clears after 5 minutes.

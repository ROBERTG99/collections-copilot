# 0006. LangGraph checkpointer with interrupts versus a custom state machine

- Status: accepted
- Date: 2026-10-16

## Context

A case runs for hours or days and pauses three times: for a human approval, for a send deferred to the debtor's contact window, and for the debtor's reply. Between those pauses the worker can crash, SQS can deliver a message twice, and two deliveries for one case can arrive at once (ADR 0004). The pauses must survive restarts, a resume must reach the pause it was meant for, and no step may repeat a side effect.

## Decision

- The case graph is a LangGraph `StateGraph` compiled with `AsyncPostgresSaver` (`src/collections_copilot/agent/case_graph.py:751`; `src/collections_copilot/agent/checkpointer.py:23-49`). Checkpoints live in schema `checkpoints` of the main Postgres database, through the same psycopg 3 driver as the application (docs/DECISIONS.md, 2026-10-16, psycopg). `thread_id` is the case id.
- The three pauses are `interrupt()` calls with a `kind`: `approval` (`src/collections_copilot/agent/case_graph.py:442`), `deferred_send` (`src/collections_copilot/agent/case_graph.py:515`) and `await_reply` (`src/collections_copilot/agent/case_graph.py:665`). The approval API, the scheduler and the reply graph resume a case by enqueueing `resume_case` (`src/collections_copilot/api/routers/approvals.py:302`, `src/collections_copilot/agent/scheduler.py:231`, `src/collections_copilot/agent/reply_graph.py:375`).
- `CaseRunner` reads the checkpoint before acting (`src/collections_copilot/worker/runner.py:68`). A `process_case` for a thread that exists is either recovered or ignored as a duplicate. A resume must name the interrupt kind, and it is applied with `Command(resume=...)` only if the graph is paused at that kind (`src/collections_copilot/worker/runner.py:92`, `:98`). Deliveries for one case are serialised with a Postgres advisory lock (`src/collections_copilot/worker/runner.py:56`; docs/DECISIONS.md, 2026-10-16).
- LangGraph re-runs an interrupted node from its start, so nodes are written to be idempotent (`src/collections_copilot/agent/case_graph.py:11-13`). Sends do not trust checkpointed values: they re-read the case, the draft row, its approval record and the debtor's facts (docs/DECISIONS.md, 2026-10-16), and a closed case cannot be reopened by a re-run node (`CasesRepo.set_unless_closed`, `src/collections_copilot/db/repositories/cases.py:84`).
- The reply graph has no human pause and runs without a checkpointer; its state lives in `messages` and `promises` with unique keys (docs/DECISIONS.md, 2026-10-16).

## Alternatives considered

- **A custom state machine: a `cases.state` column, a transition table and hand-written resume handlers.** Rejected (the stack needs: durable resume in the main DB). It would reimplement what the checkpointer already provides: persisted state per step, resume at the exact paused node with a value, and crash recovery from the last completed step. Each new pause would need its own persistence and resume code, and the graph structure (`src/collections_copilot/agent/case_graph.py:730-751`) would be spread over handlers instead of being one declaration.
- **Checkpointing the reply graph too.** Rejected. A reply has no human pause, and redelivery is already handled by database keys such as `ptp:<tenant>:<MessageSid>` (docs/DECISIONS.md, 2026-10-16).

## Consequences

- A paused case holds no worker; resuming is a queue message and a checkpoint read, so worker tasks can restart or scale at any time (ADR 0003). `tests/integration/test_case_graph.py::test_resume_after_worker_restart` covers recovery.
- Checkpoints are a second copy of case state. Anything that decides a side effect must be re-read from the application tables, as the send path does; trusting a checkpointed value there is a bug.
- Replays are a real cost of the model: a node before `interrupt()` runs again on resume, so its writes need keys. `tests/integration/test_send_guards.py::test_approval_on_closed_case_closes_without_send` found such a case.
- A wrong or missing resume kind is refused, not guessed (`tests/integration/test_reply_idempotency.py::test_resume_without_kind_is_rejected`).
- Paused threads store node names. Renaming or removing a node where cases can be paused needs a plan for the threads already waiting there. If checkpoint size or migration of paused threads becomes a recurring problem, this decision should be revisited.

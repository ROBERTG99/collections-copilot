# Design FAQ

Short answers to the design questions a reviewer usually asks, each pointing at the code that
implements the answer. Paths are relative to the repository root; line numbers refer to the
current tree. Gaps are tracked in [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).

## Can a message be sent without approval?

Not a collection message. The only call that sends a drafted message is in `send_sms`
(`src/collections_copilot/agent/case_graph.py:518-537`). It ignores the checkpoint, re-reads the
case, the draft row for the exact version and its approval record
(`src/collections_copilot/agent/store.py:234-251`), and blocks with an `sms.blocked` audit event
unless the decision is `approve`, `edit` or `auto_approve`. It then runs the compliance post-check
again on the stored text (`src/collections_copilot/agent/case_graph.py:540-548`) and claims the
outbound row under a lock on the case, re-checking the opt-out
(`src/collections_copilot/agent/store.py:327-357`). Only after that claim does it call the SMS
provider (`src/collections_copilot/agent/case_graph.py:605`).

Approval records come from two places only:

- A human, through the approval API, which locks the case, rejects stale versions and re-runs the
  post-check before storing the record (`src/collections_copilot/api/routers/approvals.py:143-289`).
- The auto-approval policy, evaluated on facts read at that moment
  (`src/collections_copilot/agent/case_graph.py:191-210`, `src/collections_copilot/agent/policy.py:58-90`):
  only `remind`, the house rules must independently choose `remind`, a clean post-check, a
  convertible single-currency balance at or below the configured maximum, and model confidence at or
  above the minimum. Rule and template fallbacks always need a human. An auto-approval is re-evaluated at send
  time and revoked if it no longer qualifies (`src/collections_copilot/agent/case_graph.py:549-561`).

The one exception is the STOP confirmation: a fixed template (`stop_confirmation`) sent once per
tenant and number when an opt-out is created, with no approval record
(`src/collections_copilot/agent/reply_actions.py:26-83`).

## What stops an injected note from triggering a send?

The model never holds the send permission, so an injected note can at most change a proposal.

- Notes reach only the next-best-action prompt, fenced in `<untrusted_note>` blocks after the
  instructions; `untrusted_note` tags inside the note are replaced and `<`, `>`, `&` are escaped
  (`src/collections_copilot/agent/prompts.py:81-88`). The draft prompt gets no notes at all
  (`src/collections_copilot/agent/nodes/draft.py:98`).
- The NBA output is schema-validated and may cite only note ids that exist
  (`src/collections_copilot/agent/nodes/nba.py:67-69`).
- Auto-approval does not trust the model's choice or confidence: the deterministic rules must reach
  `remind` on their own (`src/collections_copilot/agent/policy.py:70-77`). Anything else waits for a
  human.
- Whatever text is approved must pass the post-check: amounts must exist in the ledger, links must be
  on the allowlist, forbidden phrases and disputed invoices block
  (`src/collections_copilot/compliance/engine.py:110-145`,
  `src/collections_copilot/compliance/rules/links.py:13`).
- Ledger tool results are treated as untrusted data and never placed in prompts
  (`src/collections_copilot/integrations/mcp/ledger.py:1-6`).

The `compliance` eval suite drives adversarial cases through the full graph with a careless
approver (`src/collections_copilot/evals/targets.py:1-12`); its results are in evals/REPORT.md.

## How is a duplicate ledger write prevented on redelivery?

Three layers use the same key, `ptp:<tenant>:<MessageSid>`
(`src/collections_copilot/agent/nodes/record_promise.py:27-28`):

1. The local `promises` row is inserted with `ON CONFLICT DO NOTHING` on its unique
   `idempotency_key` before the ledger call (`src/collections_copilot/db/repositories/ops.py:58-67`),
   so a redelivered reply finds the existing row and stops if it is final or held
   (`src/collections_copilot/agent/nodes/record_promise.py:137-154`). Applying the result locks the
   row and only moves a promise that is still open
   (`src/collections_copilot/agent/nodes/record_promise.py:52-62`).
2. mcp-gateway stores the result per key and replays it for the same arguments, or returns `-32040
   idempotency_conflict` for different arguments
   (`third_party/mcp-gateway/src/mcpgw_lite/server.py:236-266`).
3. The ledger itself has `UNIQUE (tenant, idempotency_key)` and inserts with `ON CONFLICT DO NOTHING`
   (`apps/ledger-mcp/src/ledger_mcp/store.py:26-32`, `apps/ledger-mcp/src/ledger_mcp/store.py:114-117`).

Around that, the worker takes an advisory lock per reply
(`src/collections_copilot/worker/main.py:121-126`). The promise and STOP paths rely on their own keys; the
other reply handlers mark the message `handled` in the same transaction as their side effects
(`src/collections_copilot/db/repositories/ops.py:195-207`, for example
`src/collections_copilot/agent/reply_graph.py:303-311`).

## What happens on `approval_required`?

The gateway policy holds `ledger.record_promise_to_pay` for amounts above 500000 cents or dates more
than 60 days out (`deploy/mcpgw/policy/collections.yaml:16-19`). The flow:

1. The gateway creates an approval, posts a signed webhook to the API and answers with
   `status: approval_required` and an `approval_id`
   (`third_party/mcp-gateway/src/mcpgw_lite/server.py:302-334`), which the client maps to a
   `LedgerResult` (`src/collections_copilot/integrations/mcp/ledger.py:130-139`).
2. The promise moves to `approval_required` with the `mcp_approval_id`, audited
   (`src/collections_copilot/agent/nodes/record_promise.py:86-90`). The webhook links the approval to
   the promise and e-mails a supervisor once
   (`src/collections_copilot/api/routers/webhooks.py:274-337`).
3. A supervisor calls `POST /api/v1/promises/{id}:approve` or `:reject`. Approve refuses if no
   invoice is open any more, records the decision at the gateway, audits, commits and then enqueues
   `retry_write_back` (`src/collections_copilot/api/routers/promises.py:116-165`). Reject marks the
   promise `rejected` (`src/collections_copilot/api/routers/promises.py:168-198`). An expired gateway
   approval returns 409 `approval_expired` (`src/collections_copilot/api/routers/promises.py:221-224`).
4. The retry sends the same idempotency key plus `_meta.approval_id`
   (`src/collections_copilot/agent/nodes/record_promise.py:166-211`); the gateway checks that the
   approval matches tenant, key, arguments, tool and agent, and is approved and unexpired
   (`third_party/mcp-gateway/src/mcpgw_lite/server.py:268-301`).

## How are quiet hours computed across time zones and DST?

The window is 08:00 to 21:00 in the debtor's IANA zone, and sends stop at 20:50, a 10-minute
buffer before the close (`src/collections_copilot/compliance/rules/window.py:12-15`). `now` must
be timezone-aware; it is converted with `zoneinfo`, and outside the window the next send is 08:00 local, built in the zone and
converted back (`src/collections_copilot/compliance/rules/window.py:23-40`). Because the wall-clock
time is constructed in the zone, DST changes need no special case; the spring-forward case is tested
(`tests/unit/compliance/test_rules.py:61-65`).

The window never blocks: it produces a non-blocking `outside_contact_window` note and a `send_at`
(`src/collections_copilot/compliance/engine.py:90-102`). The graph pauses at `wait_send_window`
with a `deferred_send` follow-up (`src/collections_copilot/agent/case_graph.py:508-516`), the
scheduler resumes it (`src/collections_copilot/agent/scheduler.py:197-198`), and `send_sms`
checks the window again at send time and defers again if needed
(`src/collections_copilot/agent/case_graph.py:562-566`).

## Why is the score not from the LLM?

The score orders the work queue, so it has to be reproducible, explainable and immune to text in
notes. It is a fixed formula over balance, days past due, broken promises and
segment (`src/collections_copilot/scoring/priority.py:1-9`, `src/collections_copilot/scoring/priority.py:42-55`).
The `prioritize` node computes it first and only then asks the model for a rationale, which
explains the score and never changes it; on failure the rationale is simply empty
(`src/collections_copilot/agent/case_graph.py:249-266`, `src/collections_copilot/agent/nodes/rationale.py:1-28`).
An import-linter contract forbids `scoring` and `compliance` from importing the LLM or agent layers
(`pyproject.toml:166-170`).

## How is PII kept from the provider?

- The gateway redacts the user turn before any provider call; the system prompt is static and holds
  no case data (`src/collections_copilot/llm/gateway.py:276-277`).
- The redactor runs Presidio offline with pattern recognizers for phone numbers, e-mails, cards and
  IBANs, plus a deny list per account: the company name, its short forms, contact names when given,
  and the account phone (`src/collections_copilot/llm/redaction.py:111-130`,
  `src/collections_copilot/llm/redaction.py:142-184`).
- Model output gets only the account-name placeholder restored; any other placeholder is removed
  (`src/collections_copilot/llm/redaction.py:186-193`, `src/collections_copilot/agent/nodes/draft.py:110-115`).
- Audit payloads are redacted separately (`src/collections_copilot/db/audit.py:43-53`).

Known gap: person names inside free-text notes are not recognised
(`docs/KNOWN_LIMITATIONS.md:12`, `docs/KNOWN_LIMITATIONS.md:20`).

## How is cost per account bounded?

By structure; there is no runtime dollar cap per account.

- The number of calls per case is fixed: one rationale, one NBA call only when the account has
  notes (`src/collections_copilot/agent/nodes/nba.py:61-63`), and at most two drafts before the case
  goes to a human (`src/collections_copilot/agent/case_graph.py:401-407`). A re-run after a crash
  reuses the stored draft instead of calling again (`src/collections_copilot/agent/case_graph.py:333-347`).
- Each route has a fixed model and `max_tokens` (`src/collections_copilot/llm/gateway.py:68-79`);
  a call makes at most two validation rounds, each with bounded retries
  (`src/collections_copilot/llm/gateway.py:184-205`, `src/collections_copilot/llm/gateway.py:289-341`).
- Inputs are bounded: the 20 most recent notes (`src/collections_copilot/agent/store.py:192`) and
  reply bodies cut at 1600 characters (`src/collections_copilot/api/routers/webhooks.py:41`). A
  redelivered reply reuses its stored classification (`src/collections_copilot/agent/reply_graph.py:248-253`).
- Cost is measured: every call records tokens and cost as a step record
  (`src/collections_copilot/llm/gateway.py:358-387`), and the ops metrics expose cost per account (`src/collections_copilot/api/routers/metrics.py:72`). The live NBA gate fails
  above USD 0.02 per case (`src/collections_copilot/evals/live.py:43`,
  `src/collections_copilot/evals/live.py:199-208`); offline runs cost nothing.

## What happens when Bedrock is down?

Cases keep moving on deterministic paths, and nothing is sent without a human decision.

- The gateway retries only timeouts, 408/409/429/5xx and connection errors, with backoff
  (`src/collections_copilot/llm/gateway.py:184-205`). After 5 consecutive failed calls the circuit
  breaker opens and calls return `circuit_open` without reaching the provider
  (`src/collections_copilot/llm/gateway.py:128-146`, `src/collections_copilot/llm/gateway.py:271-274`).
  Timeout, retries and breaker settings come from `CC_LLM_*`
  (`src/collections_copilot/config.py:57-60`); the breaker is per worker process.
- Callers fall back: NBA uses the house rules labelled `fallback`
  (`src/collections_copilot/agent/nodes/nba.py:107-115`), drafts use templates
  (`src/collections_copilot/agent/nodes/draft.py:115`), the rationale is left empty, and a reply is
  classified `other` and routed to an analyst (`src/collections_copilot/agent/reply_graph.py:140-144`).
  STOP is a rule and never needs the model.
- A fallback is never auto-approved (`src/collections_copilot/agent/case_graph.py:208-209`).

Timeouts and provider errors are returned as a result status rather than raised
(`src/collections_copilot/llm/gateway.py:196-201`), so these paths finish normally and an outage
does not by itself push messages to the DLQs.

## How is tenant isolation enforced?

- For API requests the tenant comes only from the verified token's `custom:tenant_id`; a token
  without one is rejected (`src/collections_copilot/api/deps/auth.py:104-113`).
- Data access goes through repositories that take the tenant as the first argument and filter on it
  (`src/collections_copilot/db/repositories/base.py:1`, for example
  `src/collections_copilot/db/repositories/ops.py:34-51`). Raw SQL outside `collections_copilot.db`
  is banned by ruff (`pyproject.toml:105-106`), and both are tested
  (`tests/integration/db/test_tenant_scoping.py:13-37`).
- The worker refuses a task whose tenant does not own the checkpointed case
  (`src/collections_copilot/worker/runner.py:69-70`); the gateway webhook checks the promise's tenant
  (`src/collections_copilot/api/routers/webhooks.py:308-309`).
- The audit chain is per tenant (`src/collections_copilot/db/audit.py:119-129`).

Not enforced by the database: there is no row-level security and no composite `(tenant_id, id)`
foreign keys (`docs/KNOWN_LIMITATIONS.md:8`).

## How is the audit chain verified?

Each event's hash is `sha256(prev_hash + canonical JSON of the record)`, with 64 zeros as genesis
(`src/collections_copilot/db/audit.py:74-85`). Appends take a per-tenant advisory lock so the
chain stays linear (`src/collections_copilot/db/audit.py:107-155`), and a trigger rejects `UPDATE`,
`DELETE` and `TRUNCATE` on `audit_events` (`migrations/versions/0001_initial.py:19-29`).

Verification walks a tenant's events in `seq` order and returns the first sequence number whose link
or content is wrong (`src/collections_copilot/db/audit.py:97-104`). Two entry points use it:
`uv run cc audit verify [--tenant <id>]`, which exits 1 with `first_bad_seq`
(`src/collections_copilot/cli.py:32-64`; it also prints each tenant's head to keep elsewhere), and `GET /api/v1/audit?verify=true` for a supervisor
(`src/collections_copilot/api/routers/audit.py:74-76`).

The chain cannot show that trailing events were deleted by someone able to disable the trigger,
unless the head `(seq, hash)` was exported earlier (`docs/KNOWN_LIMITATIONS.md:9`).

## How are webhook replays handled?

Twilio requests are verified first (`X-Twilio-Signature` over the public URL and form) and refused
with 403 otherwise (`src/collections_copilot/api/routers/webhooks.py:139-141`). Twilio signatures
carry no timestamp, so replays are handled by deduplication:

- Inbound replies are stored once per `MessageSid` (unique `provider_sid`,
  `src/collections_copilot/db/repositories/ops.py:180-193`); a replay of a handled reply returns 204
  without queueing (`src/collections_copilot/api/routers/webhooks.py:160-173`).
- Status callbacks are stored once per `(MessageSid, MessageStatus)` and only move a message
  forward (`src/collections_copilot/api/routers/webhooks.py:116-130`).
- A replayed STOP is a no-op: the opt-out insert is idempotent
  (`src/collections_copilot/db/repositories/ops.py:297-308`) and the confirmation is sent at most
  once (`src/collections_copilot/agent/reply_actions.py:76-83`).

The mcp-gateway webhook is HMAC-SHA256 over `{timestamp}.{raw body}`; a bad signature or a
timestamp more than `MCPGW_MAX_SKEW_S` = 300 seconds off is refused and audited (throttled)
(`src/collections_copilot/api/routers/webhooks.py:271-295`). A replay inside that window is
recognised from the audit log by `approval_id` and answered without a new event or e-mail
(`src/collections_copilot/api/routers/webhooks.py:310-321`, `src/collections_copilot/db/audit.py:188-204`).

## Why are offline eval numbers meaningful?

They measure the pipeline and its guardrails, not model quality. Offline runs use the real code
paths with the keyword `FakeLLM`: the NBA suite runs the case graph, the replies target calls the
reply graph's own decision functions, and the compliance target drives the full graph with a
careless approver while recording every SMS, ledger call and provider request
(`src/collections_copilot/evals/targets.py:1-12`). The write-back decision the eval spies on is
the same function the graph uses (`src/collections_copilot/agent/reply_graph.py:73-76`). So
guardrail metrics (compliance violations, unauthorized write-backs, PII leaked, STOP recall) say
what the code does with any classification.

Limits: labels are by construction on templated synthetic data whose dev and test splits share
templates, so accuracy figures are not estimates of a real model or of generalisation
(`docs/KNOWN_LIMITATIONS.md:17`). The replies test split fails one gate offline
(`docs/KNOWN_LIMITATIONS.md:18`); current values are in evals/REPORT.md. `make eval-live` measures a
real model within `LIVE_EVAL_BUDGET_USD`.

## How does the worker scale on AWS?

The worker is a Fargate service with 1 to `worker_max_tasks` tasks
(`infra/terraform/envs/dev/main.tf:67-82`). Two target-tracking policies apply: CPU, and the
`cc-tasks` backlog per running task, computed as visible messages divided by
`RunningTaskCount` with a target of 20 (`infra/terraform/modules/ecs_service/main.tf:199-268`).
The `cc-replies` queue is consumed by the same tasks but is not a scaling signal.

Inside a task, one consumer per queue long-polls with concurrency bounded by
`CC_WORKER_CONCURRENCY` (`src/collections_copilot/config.py:86`,
`src/collections_copilot/worker/main.py:129-142`, `src/collections_copilot/worker/consumer.py:78-99`);
failed messages are left to become visible again and reach the DLQ after the maximum receive count
(`src/collections_copilot/worker/consumer.py:62-63`). More tasks are safe because deliveries for
one case are serialised by an advisory lock (`src/collections_copilot/worker/main.py:111-115`,
`src/collections_copilot/worker/runner.py:56-57`) and only the task holding the scheduler's
leader lock runs follow-ups (`src/collections_copilot/agent/scheduler.py:74-76`).

## What changes for production?

Nothing here has been planned or applied against an AWS account
(`docs/KNOWN_LIMITATIONS.md:26`). Before production:

- One Twilio number or Messaging Service per tenant, routing inbound by `To`
  (`docs/KNOWN_LIMITATIONS.md:13`).
- The api and the worker share one KMS signing key and choose their own scopes; the gateway
  verifies them with the key the api publishes at `/.well-known/agent-jwks.json`
  (`docs/KNOWN_LIMITATIONS.md:21`).
- Published evalgate and mcp-gateway releases instead of the `third_party/` fallbacks, with the
  gateway signing timestamps (`docs/KNOWN_LIMITATIONS.md:7`, `docs/KNOWN_LIMITATIONS.md:14`).
- Composite tenant foreign keys and an external anchor for the audit head
  (`docs/KNOWN_LIMITATIONS.md:8-9`).
- A person-name recogniser for redaction (`docs/KNOWN_LIMITATIONS.md:20`).
- Infrastructure: an ElastiCache module, ALB access logs, an S3 endpoint policy, a protected
  environment in the plan role's trust, and SMTP with authentication and TLS
  (`docs/KNOWN_LIMITATIONS.md:26`, `docs/KNOWN_LIMITATIONS.md:33-34`); RDS runs a single instance
  unless `multi_az` is set (`infra/terraform/modules/rds/main.tf:87`,
  `infra/terraform/modules/rds/main.tf:105`).
- Live evals with recorded cassettes to measure a real model (`docs/KNOWN_LIMITATIONS.md:18`).

Operations for the AWS deployment are in [runbook.md](runbook.md).

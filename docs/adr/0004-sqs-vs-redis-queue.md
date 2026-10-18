# 0004. SQS versus a Redis queue

- Status: accepted
- Date: 2026-10-16

## Context

The api hands work to the worker on two queues: `cc-tasks` (`process_case`, `resume_case`, `follow_up`, `retry_write_back`) and `cc-replies` (inbound SMS). A message can start an SMS send or a ledger write-back, so a lost message stalls a case and a duplicate must not repeat a side effect. Redis 7 is already in the stack for SSE progress streams, rate limits and `Idempotency-Key` results (docs/DECISIONS.md, 2026-10-16 and 2026-10-17). In AWS the worker must scale on queued work (ADR 0003).

## Decision

- Both queues are SQS, with a dead-letter queue each. Locally they are ElasticMQ (`deploy/elasticmq.conf`); in AWS they come from `infra/terraform/modules/sqs` with redrive after `max_receive_count` receives (default 5, `infra/terraform/modules/sqs/variables.tf:18-26`) and KMS on the work queues and the DLQs (`infra/terraform/modules/sqs/main.tf:6`, `:14`).
- One adapter, `SqsQueueClient`, behind the `QueueClient` protocol (`src/collections_copilot/integrations/queue/sqs.py:29-45`), uses `aiobotocore` (docs/DECISIONS.md, 2026-10-16). It carries the trace context in message attributes (`src/collections_copilot/integrations/queue/sqs.py:75`) and turns an unparseable body into a poison marker so one bad message does not fail the batch (`src/collections_copilot/integrations/queue/sqs.py:104-111`).
- The consumer deletes a message only after its handler succeeds; a failure leaves it to become visible again and, after the receive limit, to move to the DLQ (`src/collections_copilot/worker/consumer.py:53-76`).
- Delivery is at least once, and duplicates are absorbed in Postgres, not in the queue: the runner reads the checkpoint before acting (`src/collections_copilot/worker/runner.py:68`), deliveries for one case are serialised with an advisory lock (`src/collections_copilot/worker/runner.py:56`), and every side effect has a database key (`sms:<case_id>:<draft_version>`, `ptp:<tenant>:<MessageSid>`, spec §4.7).
- When SQS cannot be reached, `POST /cases` answers 503 with `Retry-After` and the cases stay `queued` (`src/collections_copilot/api/routers/cases.py:109-116`).

## Alternatives considered

- **Redis queues (lists or streams) on the Redis already in the stack.** Rejected per §5 (stack table: "redrive, ECS scaling signal"). Redis has no built-in redrive: a dead-letter path, a receive count and visibility timeouts would be hand-written on top of lists, or managed through consumer groups and pending-entry claims on streams. The ECS backlog metric (`ApproximateNumberOfMessagesVisible`) and the `CCDLQNotEmpty` alarm (`infra/terraform/modules/observability/main.tf:22-28`) come from SQS without code. Redis is also the component the app already treats as best effort: rate limits fail open when it is down (docs/DECISIONS.md, 2026-10-17), and envs/dev has no ElastiCache module (docs/KNOWN_LIMITATIONS.md, Terraform scope). Work that leads to an SMS or a ledger write should not depend on it.

## Consequences

- Retries, the DLQ and the scaling signal are configuration, tested with `terraform test` (`infra/terraform/envs/dev/tests/encryption.tftest.hcl:116-120` checks the data key on every queue and DLQ and the redrive count).
- SQS does not deduplicate standard-queue deliveries, so every handler must stay idempotent against the database. A new task kind without its own key or checkpoint check is a bug; `tests/integration/test_reply_idempotency.py` and `tests/integration/test_case_graph.py` cover the existing ones.
- Messages are JSON objects without ordering; anything that depends on order reads the database.
- Queue depth for the DLQ card is platform-wide, because the DLQs are shared by all tenants (docs/KNOWN_LIMITATIONS.md, Ops metrics).
- ElasticMQ is not SQS: the local double could accept something AWS refuses. `sqs-limits = strict` in `deploy/elasticmq.conf` narrows the gap. The SQS contract test (`tests/contract/test_sqs_contract.py:14-16`) runs the adapter against ElasticMQ and moto: send, receive, the in-flight message staying hidden, the visibility reset, the receive count, delete, and the trace context in the message attributes.

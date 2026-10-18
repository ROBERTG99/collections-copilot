# 0003. ECS Fargate versus EKS versus Lambda for the worker

- Status: accepted
- Date: 2026-10-17

## Context

The worker is one long-running process (`src/collections_copilot/worker/main.py:87-177`). It runs:

- two SQS consumers (`cc-tasks` for the case graph, `cc-replies` for the reply graph) with long polling, bounded concurrency, and a visibility extension while a handler runs (`src/collections_copilot/worker/consumer.py:45-51`, `:78-99`);
- the follow-up scheduler, led by one worker at a time through a session-level Postgres advisory lock (`LeaderLock`, `src/collections_copilot/db/session.py:55`; `src/collections_copilot/worker/main.py:150-156`);
- a DLQ depth watcher for the `CCDLQNotEmpty` alert (`src/collections_copilot/worker/main.py:180-191`) and `/healthz` and `/metrics` on port 8311.

Each case run holds a per-case `pg_advisory_xact_lock` on a pooled connection for the whole run (`src/collections_copilot/db/session.py:43-52`; docs/DECISIONS.md, 2026-10-16). The checkpointer keeps its own psycopg pool (`src/collections_copilot/agent/checkpointer.py:30-38`). Human pauses are LangGraph interrupts stored in Postgres (ADR 0006), so no process waits for an approval. The other four services (api, web, ledger-mcp, mcp-gateway) are containers built from the same Dockerfiles that compose uses.

## Decision

- All five services, including the worker, are ECS services on Fargate (`infra/terraform/modules/ecs_service/main.tf:98`, `:149`): private subnets, no public IP, Service Connect between services, and a deployment circuit breaker with rollback (`infra/terraform/modules/ecs_service/main.tf:155`).
- The worker runs the `worker` target of `docker/python.Dockerfile:54-58`, the same image as in compose.
- The worker scales between 1 and `worker_max_tasks` tasks on SQS backlog per running task, computed from `ApproximateNumberOfMessagesVisible` and `RunningTaskCount` (`infra/terraform/modules/ecs_service/main.tf:212-268`; `infra/terraform/envs/dev/main.tf:77`). Each worker task runs one process with `CC_WORKER_CONCURRENCY` slots per queue, and the service scales out by tasks.
- Lambda is used where a short stateless hook fits: the Cognito pre-token trigger (`infra/terraform/modules/cognito/lambda/pre_token.py`).

## Alternatives considered

- **EKS.** Rejected. It adds a cluster control plane, node groups or Fargate profiles, an ingress controller and a second deployment toolchain (manifests or Helm) for five services, and none of these gives the worker a property it needs. The build container has no kubectl or Helm, and get.helm.sh and registry.k8s.io are blocked (spec Appendix A), so that setup could not be validated offline the way `make tf-check` validates the ECS configuration.
- **Lambda with an SQS event source.** Rejected. The worker depends on being a long-lived process. The scheduler's leader lock and the per-case advisory lock live on database connections held for the length of a run, and the checkpointer keeps a connection pool; short-lived execution environments would open connections per invocation, and the configuration has no RDS Proxy. The scheduler, the DLQ watcher and the metrics endpoint would each need another trigger or service. A case run includes model calls with timeouts and retries, so the function timeout and the queue visibility timeout would have to cover the worst-case run.

## Consequences

- The worker in AWS is the same image and entry point as in `make up`, so local and integration tests exercise the deployed code path.
- At least one worker task is always running, and paying, even when the queues are empty (`scaling = { min = 1, ... }` in `infra/terraform/envs/dev/main.tf:77`).
- Backlog scaling reacts to queue depth, not to graph duration; cases paused at an interrupt use no task.
- The per-case lock keeps a pooled connection idle in a transaction for a whole run, so the database role must not set `idle_in_transaction_session_timeout` and the pool must stay larger than `CC_WORKER_CONCURRENCY` (docs/DECISIONS.md, 2026-10-16).
- Whether the Service Connect proxy in services without internet egress needs an ECS control-plane endpoint is not verified (docs/KNOWN_LIMITATIONS.md, ECS networking). If tasks cannot scale fast enough for the backlog, or the always-on minimum dominates cost, this decision should be revisited.

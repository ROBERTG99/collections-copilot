# Runbook

Operations for the AWS deployment described by `infra/terraform/envs/dev` and for the local
Docker Compose stack.

**Status: the Terraform configuration has never been applied, nor planned against an AWS account,
from this repository.** It is validated offline only (`make tf-check`, `make tf-test`, mock
provider). The AWS procedures below are written for the operator who deploys it; nothing in this
repository runs the apply step, and the plan role available to CI is read-only. Known gaps of the
AWS configuration are listed in [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) (rows "Terraform
scope", "Edge and network", "ECS networking", "Agent token scopes", "Notifications").

## What is deployed

From [envs/dev/main.tf](../infra/terraform/envs/dev/main.tf):

- ECS Fargate cluster with five services: `api`, `worker`, `web`, `ledger-mcp`, `mcp-gateway`
  (private subnets, Service Connect between them). The ALB sends the console to `web` and only
  `/api/v1/webhooks/twilio/*` to `api`.
- RDS PostgreSQL (private, KMS-encrypted, deletion protection, RDS-managed master password,
  single instance unless `multi_az` is set).
- SQS work queues `cc-tasks` and `cc-replies`, each with a dead-letter queue (`cc-tasks-dlq`,
  `cc-replies-dlq`).
- Secrets Manager entries (empty), ECR repositories (immutable tags, last 30 images kept),
  Cognito, KMS keys, CloudWatch log groups, alarms to an SNS topic, a monthly budget, and a
  read-only GitHub OIDC plan role.
- Not created: Redis (`CC_REDIS_URL` points at one created separately), a database migration task,
  and an SMTP relay (`smtp_host` is an input).

## Manual deploy

1. Choose an immutable image tag, normally the git SHA of the commit being deployed. `image_tag`
   rejects `latest`, and ECR refuses to overwrite an existing tag.
2. Build the five images from the repository root (the same Dockerfiles and targets as
   `.github/workflows/docker.yml`). `REGISTRY` is the account's ECR registry, as in the
   `ecr_repository_urls` output; repositories are named `collections-copilot/<service>`.

   ```bash
   TAG=$(git rev-parse HEAD)
   for svc in api worker ledger-mcp mcp-gateway; do
     docker build -f docker/python.Dockerfile --target "$svc" -t "$REGISTRY/collections-copilot/$svc:$TAG" .
   done
   docker build -f web/Dockerfile --target web -t "$REGISTRY/collections-copilot/web:$TAG" .
   ```

3. Log in to ECR with your own operator credentials and push:

   ```bash
   aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY"
   for svc in api worker web ledger-mcp mcp-gateway; do
     docker push "$REGISTRY/collections-copilot/$svc:$TAG"
   done
   ```

4. Plan. The `plan` job in `.github/workflows/terraform.yml` runs on `main` when the repository
   variable `AWS_PLAN_ROLE_ARN` is set (the `github_plan_role_arn` output). It assumes the
   read-only role from `modules/github_oidc`, which cannot apply and is denied data reads, and
   publishes only the `Plan:` line. That job uses `terraform init -backend=false` and
   `terraform.tfvars.example`, so it checks the configuration against the account but does not
   diff against deployed state. For the plan that will be applied, the operator enables the S3
   backend commented in `versions.tf`, copies `terraform.tfvars.example` to `terraform.tfvars` with
   real values, and plans with `image_tag` set to the new tag.
5. Apply: the apply step, run by a human operator with their own credentials, from the reviewed
   plan. The repository never runs it (`.claude/rules/terraform.md`).
6. First deploy only:
   - Set every secret value (see [Secret rotation](#secret-rotation)) before the services can
     start.
   - Run the migrations. Terraform defines no migration task: run `alembic upgrade head` once as a
     one-off ECS task from the `api` image (it contains `migrations/` and `alembic.ini`), with
     `CC_DATABASE_URL` from the `database-url` secret. Do the same before deploying an image that
     adds a revision under `migrations/versions/`.
   - Point the console hostname at the `alb_dns_name` output.
7. Check: every ECS service reaches its desired count with healthy tasks, the ALB targets for `web`
   and `api` pass `/healthz`, and no alarm is in `ALARM`.

## Rollback

- Automatic: every service has the ECS deployment circuit breaker with rollback enabled, and
  deploys keep the old tasks until new ones are healthy (minimum healthy 100 percent, maximum 200)
  (`infra/terraform/modules/ecs_service/main.tf:153-158`). A deployment whose tasks fail their
  health checks rolls back to the last completed deployment by itself; confirm in the service's
  deployment events.
- Manual: redeploy the previous image tag. Set `image_tag` to the last good tag, plan, and have the
  apply step run by a human operator. `image_tag` is shared by all five services, so they roll back
  together. ECR keeps the last 30 images per repository.
- The schema is not rolled back with the image. Before rolling back across a migration, check that
  the previous image works with the current schema; downgrade only deliberately.
- Work in flight is safe to redeliver: SQS messages not deleted by a stopped task become visible
  again, and the task and reply handlers are idempotent (`src/collections_copilot/worker/runner.py`,
  `src/collections_copilot/agent/reply_graph.py`).

## DLQ inspection and replay

A message moves to its DLQ after 5 receives without success (`max_receive_count`; the same value
in `deploy/elasticmq.conf`). The worker leaves a message undeleted whenever its handler raises,
logging `handler failed` with the queue and SQS message id. DLQs keep messages for 14 days.

Two commands handle a DLQ (`src/collections_copilot/cli.py`; the `replay-dlq` skill):
`uv run cc dlq peek --queue cc-tasks-dlq --max 10` prints one line per message,
`msg_id=… task_id=… kind=… case=… tenant=… receives=…` (for `cc.reply.v1` bodies,
`reply=<message_id> sid=<provider_sid>` instead of the task, kind and case), plus
`invalid_json=true` when the body is not JSON; it never prints bodies and leaves the messages in
place. `uv run cc replay-dlq --queue cc-tasks-dlq --max 10` sends each message back to its source
queue with the same raw body and the original message attributes (the same `task_id` and trace
context; no new trace is injected), deleting it from the DLQ only after the send succeeded.

1. Inspect with `cc dlq peek` (locally, or in AWS as a one-off task from the `api` image), or
   in the SQS console with operator credentials (the GitHub plan role is denied
   `sqs:ReceiveMessage`). Bodies are `cc.task.v1` (`tenant_id`, `kind`, `case_id`,
   `resume`, `task_id`) or `cc.reply.v1` (`tenant_id`, `message_id`, `provider_sid`,
   `from_e164`, `body`). Reply bodies hold the debtor's phone number and text: do not copy them
   into tickets.
2. Find the cause in the worker logs (log group `/collections-copilot/dev/worker`) by message id:
   an invalid message, a code error, or a dependency that was down.
3. Fix the root cause first; replaying a poison message only refills the DLQ.
4. Replay with `cc replay-dlq --queue <name>-dlq --max <n>` (or the SQS console's redrive; each
   DLQ's redrive policy allows only its own source queue). Replays are safe: `process_case` ignores a duplicate or
   continues from the last checkpoint, `resume_case` resumes only a case paused at the matching interrupt, replies already
   `handled` skip their side effects, and promise write-backs reuse their idempotency key.
5. Check that the DLQ alarm returns to `OK`.

Locally, ElasticMQ serves the SQS API on `http://localhost:8340` for any SQS client, and the worker
publishes the DLQ depth:

```bash
curl -s http://localhost:8311/metrics | grep cc_sqs_dlq_depth
```

The scheduler also re-enqueues, after 2 minutes, cases still `queued` and replies still `received`
(`src/collections_copilot/agent/scheduler.py:141-168`). That recovers lost enqueues, not messages
that failed after the case or reply moved on.

## Secret rotation

Terraform creates the Secrets Manager entries without values (`modules/secrets`); values are set
out of band with operator credentials. Each service's execution role can read only the secrets its
task definition references, and the `task_secret_names` output lists them per service. Names are
`collections-copilot/dev/<name>`.

| Secret | Environment variable | Services |
|---|---|---|
| `database-url` | `CC_DATABASE_URL` | api, worker |
| `redis-url` | `CC_REDIS_URL` | api, worker |
| `twilio-account-sid` | `CC_TWILIO_ACCOUNT_SID` | api, worker |
| `twilio-auth-token` | `CC_TWILIO_AUTH_TOKEN` | api, worker |
| `mcpgw-webhook-secret` | `CC_MCPGW_WEBHOOK_SECRET`; `MCPGW_APPROVAL_WEBHOOK_SECRET` | api, worker; mcp-gateway |
| `ledger-database-url` | `LEDGER_DATABASE_URL` | ledger-mcp |
| `mcpgw-database-url` | `MCPGW_DATABASE_URL` | mcp-gateway |
| `web-session-secret` | `WEB_SESSION_SECRET` | web |

The RDS master password is separate: RDS manages it in its own secret. The database URL secrets are
not updated by RDS; change them whenever the password of the role they use changes.

Procedure:

1. Store the new value in the secret.
2. ECS injects secrets when a task starts, so running tasks keep the old value. Start a new
   deployment (force new deployment) of every service that reads the secret, and wait until the old
   tasks are gone.
3. Per secret:
   - `twilio-auth-token`: the API verifies webhook signatures with this one token. Change it at
     Twilio and redeploy api and worker together; webhooks signed with the other token are refused
     with 403 until the new tasks run.
   - `mcpgw-webhook-secret`: one value, no overlap window. Redeploy api, worker and mcp-gateway
     together. Gateway approval webhooks refused in between are audited as `webhook.bad_signature`;
     the promise still records its approval id from the tool response, but the supervisor e-mail is
     missed, so check `GET /api/v1/promises?status=approval_required` afterwards.
   - `web-session-secret`: at least 32 characters. Existing session cookies no longer decrypt, so
     every console user signs in again.
   - Database and Redis URLs: change the credential at the server, update the URL, redeploy the
     readers.
4. Check that the services are healthy and that no new `webhook.bad_signature` events appear.

## Alarms

Alarms go to the SNS topic `collections-copilot-dev-alarms` (e-mail to `alarm_email`); the
budget e-mails `alarm_email` directly. Source: `infra/terraform/modules/observability/main.tf`.

| Alarm | Condition | Action |
|---|---|---|
| `dlq-not-empty-<queue>` | visible messages in a DLQ > 0 for 5 consecutive minutes | [DLQ inspection and replay](#dlq-inspection-and-replay). |
| `oldest-message-<queue>` | oldest message in `cc-tasks` or `cc-replies` > 300 s for 5 consecutive minutes | The worker is not keeping up. Check the worker's running task count against `worker_max_tasks`, its logs for repeated `handler failed`, and RDS and Redis health. Autoscaling follows the `cc-tasks` backlog and CPU only, so a backlog on `cc-replies` alone adds no tasks. If the service is at its maximum, raise `worker_max_tasks` through a plan and the apply step. |
| `5xx-rate` | target plus load balancer 5xx > 1% of requests for 10 consecutive minutes | If it started with a deployment, roll back. Otherwise find the failing route in the api logs by `correlation_id` (every error is an RFC 9457 problem with one) and check RDS, Redis (idempotent requests fail closed without it), SQS (`queue_unavailable`) and mcp-gateway (`gateway_unavailable`). |
| Monthly budget | forecast > 80% or actual > 100% of `monthly_budget_usd` | Find the service in Cost Explorer. For model spend, a supervisor can read `llm_cost_usd` and `cost_per_account_usd` from `GET /api/v1/metrics/ops` per tenant. The code has no runtime spend cap; lowering `worker_max_tasks` slows spend but delays work. |

Locally, the `observability` profile loads the Prometheus rules in `deploy/prometheus/alerts.yml`
(`CCDLQNotEmpty`, `CCQueueLatencyP95High`, `CCApiErrorRateHigh`); Terraform does not deploy
Prometheus.

## Backups and recovery

RDS keeps automated backups for `backup_retention_days` (at least 7) and takes a final snapshot
on deletion. A point-in-time restore creates a new instance: point the database URL secrets at it
and redeploy their readers. The spec's targets (NFR-8: RTO 1 h, RPO 5 min) have not been tested.

## Local stack

| Command | What it does |
|---|---|
| `make bootstrap` | Install toolchains and dependencies; create `.env` from `.env.example`. |
| `make up` | Build and start the stack, wait until every service is healthy. |
| `make seed` | Load the synthetic dataset (gen.v1, seed 4242). |
| `make demo` | Run the scripted demo scenario against the running stack, offline. |
| `make logs` | Follow the logs of every service. |
| `make observability` | The stack plus Jaeger, Prometheus and Grafana, with trace export on. |
| `make db-shell` | `psql` on the local database. |
| `make down` | Stop the stack (every profile). |
| `make clean` | Remove containers, volumes, caches and build outputs. |

Local endpoints, all bound to 127.0.0.1: console `http://localhost:8300`, API docs
`http://localhost:8310/docs`, worker metrics `http://localhost:8311/metrics`, Mailpit
`http://localhost:8351`, Grafana `http://localhost:8391` (anonymous viewer), Jaeger
`http://localhost:8392`, Prometheus `http://localhost:8390`, ElasticMQ `http://localhost:8340`,
Postgres `localhost:8320`. Everything runs offline with `CC_LLM_PROVIDER=fake`.

## Audit chain check

The audit log is hash-chained per tenant and append-only (a trigger rejects `UPDATE`, `DELETE` and
`TRUNCATE`). Check it:

- Locally (defaults to `CC_DATABASE_URL`, the compose database):

  ```bash
  uv run cc audit verify
  uv run cc audit verify --tenant <tenant-id>
  ```

  It prints `chain_ok=true tenants=<n>` and one `head tenant=<id> seq=<n> hash=<h>` line per
  tenant, or one `chain_ok=false tenant=<id> first_bad_seq=<seq>` line per broken tenant and
  exits 1.
- In AWS: tasks have no shell (`enable_execute_command = false`). Run
  `python -m collections_copilot.cli audit verify` as a one-off task from the `api` image with
  `CC_DATABASE_URL` from the `database-url` secret, or call `GET /api/v1/audit?verify=true` as a
  supervisor (one tenant, `chain_ok` and `first_bad_seq` in the response).

Deleting trailing events with the trigger disabled is only detectable against an earlier copy
of each tenant's head, so keep the `head` lines `cc audit verify` prints (daily) outside the
database and compare: a chain that no longer passes through a recorded head was rewritten.

If the check fails: treat it as a security incident, do not write to `audit_events`, keep a
database snapshot, and compare the rows around `first_bad_seq` with the exported heads:

```sql
SELECT seq, ts, actor_type, action, entity_id, prev_hash, hash FROM audit_events
WHERE tenant_id = '<tenant-id>' AND seq >= <first_bad_seq> - 5 ORDER BY seq LIMIT 20;
```

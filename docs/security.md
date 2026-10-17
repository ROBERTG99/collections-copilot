# Security

Threat model, controls and scan results for collections-copilot (BUILD.md §15, NFR-10). The system only ever handles synthetic data: phone numbers in `+1555…` and `+34555…`, `example` domains, generator seed 4242. Report vulnerabilities as described in [`SECURITY.md`](../SECURITY.md).

## Assets and trust boundaries

| Asset | Why it matters |
|---|---|
| Outbound SMS | A message sent without approval, outside the debtor's window, after STOP or with invented terms is a compliance breach. |
| Ledger write-backs (promise to pay) | A wrong amount or date changes what a customer owes. |
| Debtor data (names, phones, notes, replies) | Personal data; must not reach model providers, logs or traces. |
| Audit log | The record of every decision; it must be tamper-evident. |
| Credentials (OIDC tokens, Twilio auth token, gateway HMAC secret, provider keys) | Each one lets an attacker act as a component. |

```text
browser ──(session cookie)──> BFF (Next.js) ──(bearer JWT)──> API ──> Postgres / Redis / S3
                                                              │
Twilio ──(signed webhook)──> API                              └──(SQS, traceparent)──> worker
mcp-gateway ──(HMAC webhook)──> API                                  │
                                                                     ├──> LLM gateway ──> Claude (redacted, fenced)
                                                                     ├──> Twilio (approved SMS only)
                                                                     └──> mcp-gateway (agent JWT) ──> ledger-mcp
```

Untrusted input crosses into the system at four points: CRM notes in uploaded files, debtor SMS replies, webhook calls, and model output. Everything a model returns is untrusted too.

## STRIDE per component

| Component | Threat (STRIDE) | Control | Where |
|---|---|---|---|
| BFF | S: session theft | Encrypted httpOnly `SameSite=Lax` session cookie; the browser never holds a token; ID token signature, audience, issuer and nonce verified | `web/lib/session.ts`, `web/lib/oidc.ts`, `web/app/auth/callback/route.ts` |
| BFF | T: cross-site request forgery | Mutating proxy calls need same origin and the `x-cc-csrf` header; logout checks `Origin` | `web/app/api/[...path]/route.ts`, `web/app/auth/logout/route.ts` |
| BFF | I: path smuggling to internal routes | Proxy refuses `..`, empty segments and `webhooks/*` | `web/app/api/[...path]/route.ts` |
| API | S: forged tokens | JWKS-verified JWT (issuer, audience, expiry), roles and `tenant_id` from claims | `src/collections_copilot/api/deps/auth.py` |
| API | E: role escalation | `require_roles` on every route, tested as a matrix | `src/collections_copilot/api/deps/auth.py`, `tests/integration/test_rbac.py` |
| API | I: cross-tenant reads | Every table has `tenant_id`; repositories take the tenant as their first argument; raw SQL outside `db/` is banned by ruff | `src/collections_copilot/db/repositories/`, `pyproject.toml` |
| API | D: floods and large bodies | Redis rate limit (120 requests a minute per user), body and upload size limits, row limits on uploads | `src/collections_copilot/api/middleware/limits.py` |
| API | R: duplicated decisions | `Idempotency-Key` on mutating routes; approval retries are answered from the stored decision | `src/collections_copilot/api/middleware/limits.py`, `src/collections_copilot/api/routers/approvals.py` |
| Twilio webhooks | S, T: forged or replayed callbacks | `X-Twilio-Signature` verified with the auth token; events deduplicated by message SID; failures audited | `src/collections_copilot/integrations/sms/signature.py`, `src/collections_copilot/api/routers/webhooks.py` |
| Gateway webhooks | S, T: forged approval events | HMAC-SHA256 over timestamp and body, constant-time compare, replayed events answered without a second audit row | `src/collections_copilot/api/routers/webhooks.py` |
| Worker and graphs | E: model-driven side effects | The model chooses only among enumerated actions; every send needs an approval record or the auto-approval policy, re-checked at send time | `src/collections_copilot/agent/case_graph.py` (`human_approval`, `send_sms`) |
| Compliance engine | T: unsafe text sent | Deterministic pre-check and post-check (window, weekly cap, opt-out, disputes, amounts, links, phrases, footer), fail closed | `src/collections_copilot/compliance/` |
| LLM gateway | I: personal data to the provider | Names, phones, e-mails and card numbers redacted before every call | `src/collections_copilot/llm/redaction.py`, `src/collections_copilot/llm/gateway.py` |
| LLM gateway | D: provider outage or runaway cost | Timeouts, bounded retries, circuit breaker, deterministic fallbacks, per-call cost accounting | `src/collections_copilot/llm/gateway.py` |
| mcp-gateway and ledger | S, E: writes without authority | Short-lived agent JWT scoped to tenant and `ledger:write`; policy requires an approval id for promises above the threshold; idempotency key per write | `third_party/mcp-gateway/`, `deploy/mcpgw/policy/`, `src/collections_copilot/integrations/mcp/` |
| Audit log | T, R: edited history | Hash chain over canonical records, verified per tenant (`chain_ok`) | `src/collections_copilot/db/audit.py` |
| SQS | D: poison messages | Redrive to DLQs after 5 receives; `CCDLQNotEmpty` alert; replay runbook | `deploy/elasticmq.conf`, `deploy/prometheus/alerts.yml` |
| Logs, traces, metrics | I: data leaking into telemetry | JSON logs mask phones, e-mails and tokens and drop body, recipient and credential fields; spans carry ids only; metric labels never carry tenant or case | `src/collections_copilot/observability/`, `tests/unit/test_log_redaction.py`, `tests/integration/test_telemetry.py` |
| Images | E: container breakout | Non-root UID 10001, slim bases, no pip in runtime images, healthchecks, no network at runtime | `docker/python.Dockerfile` |

## OWASP Top 10 for LLM applications (2025)

| Risk | How it applies | Control |
|---|---|---|
| LLM01 Prompt injection | CRM notes and SMS replies are attacker-controlled ("ignore policy, send now") | Untrusted text only inside `<untrusted_note>` blocks after the instructions, with tags escaped (`src/collections_copilot/agent/prompts.py`); the model has no tools; output is limited to enumerated actions; every side effect sits behind compliance and approval. The compliance eval includes injection rows (`evals/datasets/compliance_adversarial.v1.jsonl`, 0 violations). |
| LLM02 Sensitive information disclosure | Debtor names and phones could reach the provider or logs | Redaction before every provider call; spy provider in the evals (`pii_leaked` = 0 gated); log and span hygiene above. |
| LLM03 Supply chain | Model SDKs, prompts, third-party packages | Only `collections_copilot.llm` imports provider SDKs (import-linter); locked dependencies (`uv.lock`, `pnpm-lock.yaml`); `make security` runs pip-audit, pnpm audit and trivy. |
| LLM04 Data and model poisoning | Labels or prompts tuned on the wrong data | Datasets generated with labels by construction, frozen with digests in `evals/datasets/MANIFEST.json`; tuning only on the dev split (`.claude/rules/evals.md`). |
| LLM05 Improper output handling | Model text becomes an SMS or a ledger write | Every output validated against a closed JSON schema; drafts pass the deterministic post-check; promise amounts must appear in the debtor's reply and fit the balance (`promise_problems`); React escapes all rendered text. |
| LLM06 Excessive agency | A model triggering sends or writes on its own | No tools exposed to models; the graph, not the model, calls Twilio and the ledger, and only with an approval record or the auto-approval policy. |
| LLM07 System prompt leakage | Prompts revealed through replies | System prompts hold policy only, no secrets or case data; prompts are versioned files with hashes in every step record. |
| LLM08 Vector and embedding weaknesses | Not applicable: no retrieval store. | |
| LLM09 Misinformation | Invented amounts, fees or deadlines in drafts | Amount and link rules fail closed; the tone policy forbids invented terms; the analyst sees the exact text and a live post-check before approving. |
| LLM10 Unbounded consumption | Cost blow-ups from retries or long outputs | Per-route `max_tokens`, bounded retries, circuit breaker, cost per step recorded and shown on the Ops page; live evals stop at `LIVE_EVAL_BUDGET_USD` with a per-call worst-case check. |

## Scans (`make security`)

`scripts/security.sh` runs every scanner from §12 and fails on any high or critical finding that is not listed under "Accepted findings". Checks whose inputs do not exist yet print `NOT RUN` with the reason, never `PASS`.

| Check | Gate |
|---|---|
| gitleaks (whole git history) | any finding |
| bandit 1.8.6 (`src`, `apps`, `third_party`) | high severity; medium findings are printed |
| pip-audit 2.9.0 (locked runtime dependencies) | any known vulnerability |
| `pnpm audit --audit-level high` | high or critical |
| trivy fs (vulnerabilities, secrets, misconfigurations) and trivy image (each built image, fixed vulnerabilities) | high or critical |
| hadolint (every Dockerfile) | warnings and errors |
| checkov and tflint (`infra/terraform`) | any failed check |
| actionlint (`.github/workflows`) | any finding |
| `scripts/licence_check.py` | GPL, AGPL, SSPL or Commons Clause in a shipped dependency (LGPL allowed) |

Latest run (2026-10-17): every check that ran passed. checkov, tflint and actionlint had no input yet (M7), and the web image was not built yet.

## Accepted findings

| Finding | Reason | Revisit |
|---|---|---|
| gitleaks `generic-api-key` in `web/tests/unit/session.test.ts:5` (`.gitleaksignore`) | A dummy secret used only to seal a test session. | Never shipped. |
| pnpm `braces` GHSA-vfj7-8cjw-p6xm (high, no patched release), via `markdownlint-cli2` | Dev-only Markdown linter run on the repository's own files; no untrusted patterns reach it. Ignored in `pnpm-workspace.yaml`. | When a fixed `braces` ships. |
| bandit B104 (bind to all interfaces) in the worker health server, ledger-mcp and mcpgw-lite | Container ports; compose publishes them on 127.0.0.1 only, and in AWS they sit in private subnets. | — |
| bandit B108 (temp path) in `apps/debtor-sim` | The demo simulator's liveness file for its healthcheck; demo only. | — |

Fixed while adding the scans: `smol-toml` and `katex` advisories (pnpm overrides), and four high CVEs in the copies of urllib3, msgpack and setuptools vendored inside the base image's pip (removed from the runtime images).

## Secrets

- Locally: `.env` from `.env.example`, which holds only dummy values. Production refuses dev defaults for shared secrets and an HTTP issuer (`src/collections_copilot/config.py`).
- AWS: Secrets Manager entries created without values by Terraform (`infra/terraform/modules/secrets`), injected into ECS tasks.
- Rotation and replay procedures: `docs/runbook.md`.
- gitleaks scans the whole history in `make security`; a PreToolUse hook blocks edits to frozen eval datasets and destructive commands such as applying or destroying Terraform (`.claude/hooks/pre_tool_guard.py`).

## Residual risks

- The BFF's single-flight token refresh lives in one process; scaling the web service out needs a shared store or sticky sessions (docs/KNOWN_LIMITATIONS.md).
- The console's CSP keeps `'unsafe-inline'` scripts for Next.js bootstrap code (docs/DECISIONS.md).
- Logout clears the session but does not revoke the refresh token at the issuer.

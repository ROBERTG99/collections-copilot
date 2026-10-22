# Security

Threat model, controls and scan results for collections-copilot (NFR-10). The system only ever handles synthetic data: phone numbers in `+1555…` and `+34555…`, `example` domains, generator seed 4242. Report vulnerabilities as described in [`SECURITY.md`](../SECURITY.md).

## Assets and trust boundaries

| Asset | Why it matters |
|---|---|
| Outbound SMS | A message sent without approval, outside the debtor's window, after STOP or with invented terms is a compliance breach. |
| Ledger write-backs (promise to pay) | A wrong amount or date changes what a customer owes. |
| Debtor data (names, phones, notes, replies) | Personal data; it must not reach model providers, logs or traces beyond what is listed under "Residual risks". |
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

Untrusted input crosses into the system at four points: CRM data in uploaded files (notes, names, invoice numbers), debtor SMS replies, webhook calls, and model output. Everything a model returns is untrusted too.

## STRIDE per component

| Component | Threat (STRIDE) | Control | Where |
|---|---|---|---|
| BFF | S: session theft | Encrypted httpOnly `SameSite=Lax` session cookie; the browser never holds a token; ID token signature, audience, issuer and nonce verified | `web/lib/session.ts`, `web/lib/oidc.ts`, `web/app/auth/callback/route.ts` |
| BFF | T: cross-site request forgery | Mutating proxy calls need same origin and the `x-cc-csrf` header; logout checks `Origin` | `web/app/api/[...path]/route.ts`, `web/app/auth/logout/route.ts` |
| BFF | I, E: path smuggling to internal routes | Every path segment is checked after Next decodes it (no `/`, `\`, `.`, `..` or empty segment, no `webhooks`), and the final URL must stay under `/api/v1/` outside `/api/v1/webhooks/` | `web/lib/proxy.ts` (`apiTarget`), `web/tests/unit/proxy.test.ts` |
| BFF | D: large bodies | Request bodies read with a running 1 MiB cap (413) | `web/lib/proxy.ts` (`readCapped`) |
| API | S: forged tokens | JWKS-verified RS256 JWT (`kid`, issuer, audience, expiry), `token_use` must be `access`, roles and `tenant_id` from claims | `src/collections_copilot/api/deps/auth.py`, `tests/integration/test_rbac.py` |
| API | E: role escalation | `require_roles` on every `/api/v1` route, tested as a matrix that fails if a route is missing. `/healthz`, `/readyz`, `/metrics/` and `/.well-known/agent-jwks.json` (public keys only) are unauthenticated and never routed publicly; `/docs` and `/openapi.json` are off in production | `src/collections_copilot/api/deps/auth.py`, `src/collections_copilot/api/main.py` |
| API | I: cross-tenant reads | Every table has `tenant_id`; repositories take the tenant as their first argument; raw SQL outside `db/` is banned by ruff. The only cross-tenant lookups are by provider ids in webhooks and the scheduler | `src/collections_copilot/db/repositories/`, `pyproject.toml` |
| API | D: floods and large bodies | Redis rate limit (120 requests a minute per user, 600 per source IP for webhooks), body and upload size limits, row limits; progress streams use their own Redis pool and at most 5 per user. If Redis fails the rate limit lets requests through (availability over throttling) | `src/collections_copilot/api/middleware/limits.py`, `src/collections_copilot/api/routers/events.py` |
| API | R: duplicated decisions | `Idempotency-Key` on mutating routes, failing closed (503 `Retry-After`) when the store is down; approval retries are answered from the stored decision | `src/collections_copilot/api/middleware/limits.py`, `src/collections_copilot/api/routers/approvals.py` |
| Twilio webhooks | S, T: forged or replayed callbacks | `X-Twilio-Signature` verified with the auth token; status events stored once per (MessageSid, status); bad signatures audited per tenant (at most once a minute) or logged when no tenant can be named | `src/collections_copilot/integrations/sms/signature.py`, `src/collections_copilot/api/routers/webhooks.py` |
| Gateway webhooks | S, T: forged or replayed approval events | HMAC-SHA256 over `{timestamp}.{body}`, constant-time compare, timestamps more than 5 minutes off refused; a replay is recognised from the audit log under a row lock, so it holds while Redis is down | `src/collections_copilot/api/routers/webhooks.py`, `tests/integration/test_mcpgw_webhook.py` |
| Worker and graphs | E: model-driven side effects | The model chooses only among enumerated actions; every send needs an approval record or the auto-approval policy, re-checked at send time. Auto-approval also requires the deterministic house rules to choose the same reminder, so a note cannot talk the model into one | `src/collections_copilot/agent/case_graph.py` (`human_approval`, `send_sms`), `src/collections_copilot/agent/policy.py` |
| Compliance engine | T: unsafe text sent | Deterministic pre-check and post-check (window, weekly cap, opt-out, disputes, amounts, links, phrases, footer), fail closed. Text rules read the NFKC-normalised body; invisible format characters and non-Latin letters (outside the account name) are blocked; bare IPs are disallowed hosts | `src/collections_copilot/compliance/`, `tests/unit/compliance/test_unicode.py` |
| LLM gateway | I: personal data to the provider | The account's name (also in its JSON- and note-escaped forms) and phone (E.164 and national forms), phone numbers in grouped (spaces, dots, dashes, slashes, Unicode dashes), parenthesised and plain 9–15 digit forms, also at the end of a sentence, e-mail addresses, card numbers and IBANs are redacted before every call (see residual risks for other names); ingest turns Unicode spaces in account names into plain spaces and refuses `<`, `>`, braces, backslashes or control characters | `src/collections_copilot/llm/redaction.py`, `src/collections_copilot/llm/gateway.py` |
| LLM gateway | D: provider outage or runaway cost | Timeouts, bounded retries, circuit breaker, deterministic fallbacks, per-call cost accounting | `src/collections_copilot/llm/gateway.py` |
| SMS sender | I: texting real people | Outside production only `+1555…` and `+34555…` numbers are accepted at ingest and texted (`CC_SYNTHETIC_NUMBERS_ONLY`, which may be off only with `CC_ENV=prod`); Twilio error bodies are masked before they are stored | `src/collections_copilot/integrations/sms/`, `src/collections_copilot/ingest/schemas.py` |
| mcp-gateway and ledger | S, E: writes without authority | Short-lived agent JWT scoped to tenant and `ledger:write`; policy requires an approval id for promises above the threshold; idempotency key per write | `third_party/mcp-gateway/`, `deploy/mcpgw/policy/`, `src/collections_copilot/integrations/mcp/` |
| Audit log | T, R: edited history | Hash chain over canonical records, verified per tenant (`chain_ok`) | `src/collections_copilot/db/audit.py` |
| SQS | D: poison messages | Redrive to DLQs after 5 receives; `CCDLQNotEmpty` alert; replay procedure in `docs/runbook.md` (`cc dlq peek`, `cc replay-dlq`) | `deploy/elasticmq.conf`, `deploy/prometheus/alerts.yml`, `src/collections_copilot/cli.py` |
| Logs, traces, metrics | I: data leaking into telemetry | JSON logs mask phone numbers (international and national formats, also URL-encoded), e-mail addresses, JWTs, bearer and `*token=` values and API keys, and drop body, recipient and credential fields; uvicorn's access log (raw query strings) is off and the middleware logs the route template instead. Spans carry ids and the exception type only, never its message or stack; metric labels never carry tenant or case | `src/collections_copilot/observability/`, `tests/unit/test_log_redaction.py`, `tests/unit/test_telemetry_middleware.py` |
| Configuration | S: published dev secrets accepted | Dev defaults for shared secrets and an HTTP issuer are refused with `CC_ENV=prod` or whenever the public base URL is not local; KMS agent tokens need a key id; the web's dev-secret escape hatch works only on a localhost URL | `src/collections_copilot/config.py`, `web/lib/config.ts` |
| Images | E: container compromise | Non-root UID 10001, slim bases, no pip in runtime images, healthchecks; containers install nothing at runtime | `docker/python.Dockerfile` |

## OWASP Top 10 for LLM applications (2025)

| Risk | How it applies | Control |
|---|---|---|
| LLM01 Prompt injection | CRM notes, names, invoice numbers and SMS replies are attacker-controlled ("ignore policy, send now") | Notes and replies only inside `<untrusted_note>` blocks after the instructions, with `&`, `<` and `>` escaped; context JSON escapes `&`, `<` and `>`, and the account name never reaches the model (it is redacted to `<ACCOUNT_NAME>` in every escaped form, `tests/unit/llm/test_pii_spy.py`); invoice numbers are identifiers (`^[A-Za-z0-9._/-]{1,64}$`); validation feedback goes back fenced as data (`src/collections_copilot/agent/prompts.py`, `src/collections_copilot/llm/gateway.py`). The model has no tools; output is limited to enumerated actions; every side effect sits behind compliance and approval. The compliance eval includes injection rows with 0 violations (`evals/REPORT.md`). |
| LLM02 Sensitive information disclosure | Debtor names and phones could reach the provider or logs | Redaction before every provider call; spy provider in the evals (`pii_leaked` = 0 gated, `evals/REPORT.md`); log and span hygiene above. |
| LLM03 Supply chain | Model SDKs, prompts, third-party packages | Only `collections_copilot.llm` imports provider SDKs (import-linter); locked dependencies (`uv.lock`, `pnpm-lock.yaml`); `make security` runs pip-audit, pnpm audit and trivy. |
| LLM04 Data and model poisoning | Labels or prompts tuned on the wrong data | Datasets generated with labels by construction, frozen with digests in `evals/datasets/MANIFEST.json`; tuning only on the dev split (`.claude/rules/evals.md`). |
| LLM05 Improper output handling | Model text becomes an SMS or a ledger write | Every output validated against a closed JSON schema; drafts pass the deterministic post-check; promise amounts must appear in the debtor's reply and fit the balance (`promise_problems`); React escapes all rendered text. |
| LLM06 Excessive agency | A model triggering sends or writes on its own | No tools exposed to models; the graph, not the model, calls Twilio and the ledger, only with an approval record or the auto-approval policy, and auto-approval needs the deterministic rules to agree. |
| LLM07 System prompt leakage | Prompts revealed through replies | System prompts hold policy only, no secrets or case data; prompts are versioned files with hashes in every step record. |
| LLM08 Vector and embedding weaknesses | Not applicable: no retrieval store. | |
| LLM09 Misinformation | Invented amounts, fees or deadlines in drafts | Amount and link rules fail closed on the normalised text; the tone policy forbids invented terms; the analyst sees the exact text and a live post-check before approving. |
| LLM10 Unbounded consumption | Cost blow-ups from retries or long outputs | Per-route `max_tokens`, bounded retries, circuit breaker, cost per step recorded and shown on the Ops page; live evals stop at `LIVE_EVAL_BUDGET_USD` with a per-call worst-case check. |

## Scans (`make security`)

`scripts/security.sh` runs every scanner below and fails on any high or critical finding not listed under "Accepted findings". A check whose input does not exist yet prints `NOT RUN` with the reason, and the summary then says `PASS (N not run)`. A scanner that is not installed is `NOT RUN` locally and a failure under `CI` or `SECURITY_STRICT=1`. CI's `security` job (`.github/workflows/ci.yml`) runs gitleaks over the whole history, pip-audit and `pnpm audit` on every push and pull request; the image and Terraform scanners run in `make security`.

| Check | Gate |
|---|---|
| gitleaks (whole git history) | any finding |
| bandit 1.8.6 (`src`, `apps`, `third_party`) | high severity; medium findings are printed for review |
| pip-audit 2.9.0 (locked runtime dependencies) | any known vulnerability |
| `pnpm audit --audit-level high` | high or critical |
| trivy fs (vulnerabilities, secrets, misconfigurations) and trivy image (every image `docker compose config --images` lists that is built locally; fixed vulnerabilities) | high or critical |
| hadolint (every Dockerfile, default threshold: info and above) | any finding |
| checkov and tflint (`infra/terraform`) | any failed check not skipped with a reason in the configuration |
| actionlint (`.github/workflows`) | any finding |
| `scripts/licence_check.py` | GPL, AGPL, SSPL or Commons Clause in a shipped dependency (LGPL allowed) |

The latest results are summarised in the README ("Infrastructure and security checks").

## Accepted findings

| Finding | Reason | Revisit |
|---|---|---|
| gitleaks `generic-api-key` in `web/tests/unit/session.test.ts:5`, `web/tests/unit/session-helpers.test.ts:13`, `web/tests/unit/server-session.test.ts:30` and `tests/unit/test_log_redaction.py:108` (`.gitleaksignore`) | Dummy secrets that seal test sessions, and fake token strings that the log-redaction test proves are masked. | Never shipped. |
| pnpm `braces` GHSA-vfj7-8cjw-p6xm (high, no patched release), via `markdownlint-cli2` and `eslint-config-next` | Both are dev-only tools run on the repository's own files; no untrusted patterns reach them. Ignored in `pnpm-workspace.yaml`. | When a fixed `braces` ships. |
| trivy AVD-AWS-0053 (public load balancer), `infra/terraform/modules/alb/main.tf` | The console is an internet-facing site; the ALB has a WAF (managed rule sets, per-IP rate limit, logging), HTTPS with TLS 1.3 and a redirect-only port 80. | — |
| trivy AVD-AWS-0104 (egress to any IP on 443), `infra/terraform/modules/ecs_service/main.tf` | Twilio and the identity provider publish no fixed address ranges; egress is HTTPS only, through the NAT gateway, and AWS APIs go through VPC endpoints. | An egress proxy with a domain allowlist. |
| checkov skips in `infra/terraform` (32 in `make tf-check` on 2026-10-18, each with its reason inline: Multi-AZ, 30-day log retention, secret rotation, no S3 access-log bucket, replication and events, the redirect-only HTTP listener, HTTP targets behind TLS termination, and the Cognito trigger's DLQ, VPC, code signing, X-Ray and opt-in reserved concurrency) | Cost or design choices for the dev environment, stated next to each resource. | Per production hardening. |
| bandit B104 (bind to all interfaces) in the worker health server, `src/collections_copilot/api/serve.py`, ledger-mcp and mcpgw-lite | Container ports; compose publishes them on 127.0.0.1 only, and in AWS they sit in private subnets. | — |
| bandit B108 (temp path) in `apps/debtor-sim` and `src/collections_copilot/api/serve.py` | The demo simulator's liveness file for its healthcheck (demo only), and the API's Prometheus multiprocess directory in the container's writable `/tmp` (emptied at start-up). | — |
| gitleaks `generic-api-key` in `docs/adr/0001-bedrock-primary-vs-anthropic-api.md:8` (`.gitleaksignore`) | A model ID after the words "Claude API," matches the generic pattern; it is not a credential. | — |
| hadolint DL3008 (unpinned `apt-get install git`) in the builder stage of `docker/python.Dockerfile` (inline ignore) | The builder needs git to fetch the pinned evalgate tag (locked to a commit in `uv.lock`); git never reaches the runtime images, and a pinned Debian revision breaks the build when the archive replaces it with a security update. | When evalgate is consumed as a wheel. |
| security-auditor (2026-10-19, low): eval suites name `plugins:` modules that evalgate imports | The same trust level as the suites' existing `target: python:` entry; the suite files are committed and reviewed. | Restrict plugins to `collections_copilot.evals.*` if suites ever come from outside the repository. |

Fixed while adding the scans: `smol-toml` and `katex` advisories (pnpm overrides), and four high CVEs in the copies of urllib3, msgpack and setuptools vendored inside the base image's pip (removed from the runtime images).

## Secrets

- Locally: `.env` from `.env.example`, which holds only dummy values.
- AWS: Secrets Manager entries created without values by Terraform (`infra/terraform/modules/secrets`), injected into ECS tasks.
- Rotation and replay procedures: `docs/runbook.md`.
- gitleaks scans the whole history in `make security`; a PreToolUse hook blocks edits to frozen eval datasets and destructive commands such as applying or destroying Terraform (`.claude/hooks/pre_tool_guard.py`).

## Residual risks

Each is also listed in `docs/KNOWN_LIMITATIONS.md`.

- **Names of people in notes and replies** ("spoke to Laura Gómez") reach the model provider: redaction covers the account's own name and phone, contact data patterns and cards, but there is no offline person-name recogniser and the CRM import has no contact list to deny.
- **Presigned uploads** do not sign the object size; the API reads at most the configured limit of the exact version it checked (`If-Match` and a byte range), so an oversized or swapped object is refused rather than buffered.
- **Rate limits fail open** when Redis is unavailable (idempotency fails closed). Behind a proxy, uvicorn must trust the proxy's addresses (`FORWARDED_ALLOW_IPS`, set in `infra/terraform/envs/dev/main.tf:56` to the CIDRs of the public subnets the ALB runs in) or every webhook shares one bucket.
- **Sessions are stateless**: logout clears the cookie and calls the issuer's logout but does not revoke the refresh token; a stolen cookie stays valid until it expires. The BFF's single-flight refresh map is per process.
- **Agent token scopes are chosen by the caller** in KMS mode (one signing key for the API and the worker; the gateway verifies against the API's `/.well-known/agent-jwks.json`); the dev issuer mints any scope and is bound to 127.0.0.1.
- **The console's CSP** keeps `'unsafe-inline'` scripts for Next.js bootstrap code (docs/DECISIONS.md).
- **Containers are not network-isolated locally**: the compose network has internet access; nothing in the code calls out except the configured providers. Each runtime image contains every workspace package.
- **Direct API callers** can supply their own `traceparent` and `x-correlation-id` (sanitised, but trusted for correlation).

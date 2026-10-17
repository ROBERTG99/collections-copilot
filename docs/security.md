# Security

Threat model, controls and scan results for collections-copilot (BUILD.md §15, NFR-10). The system only ever handles synthetic data: phone numbers in `+1555…` and `+34555…`, `example` domains, generator seed 4242. Report vulnerabilities as described in [`SECURITY.md`](../SECURITY.md).

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
| API | E: role escalation | `require_roles` on every `/api/v1` route, tested as a matrix that fails if a route is missing. `/healthz`, `/readyz` and `/metrics/` are unauthenticated and never routed publicly; `/docs` and `/openapi.json` are off in production | `src/collections_copilot/api/deps/auth.py`, `src/collections_copilot/api/main.py` |
| API | I: cross-tenant reads | Every table has `tenant_id`; repositories take the tenant as their first argument; raw SQL outside `db/` is banned by ruff. The only cross-tenant lookups are by provider ids in webhooks and the scheduler | `src/collections_copilot/db/repositories/`, `pyproject.toml` |
| API | D: floods and large bodies | Redis rate limit (120 requests a minute per user, 600 per source IP for webhooks), body and upload size limits, row limits; progress streams use their own Redis pool and at most 5 per user. If Redis fails the rate limit lets requests through (availability over throttling) | `src/collections_copilot/api/middleware/limits.py`, `src/collections_copilot/api/routers/events.py` |
| API | R: duplicated decisions | `Idempotency-Key` on mutating routes, failing closed (503 `Retry-After`) when the store is down; approval retries are answered from the stored decision | `src/collections_copilot/api/middleware/limits.py`, `src/collections_copilot/api/routers/approvals.py` |
| Twilio webhooks | S, T: forged or replayed callbacks | `X-Twilio-Signature` verified with the auth token; status events stored once per (MessageSid, status); bad signatures audited per tenant (at most once a minute) or logged when no tenant can be named | `src/collections_copilot/integrations/sms/signature.py`, `src/collections_copilot/api/routers/webhooks.py` |
| Gateway webhooks | S, T: forged or replayed approval events | HMAC-SHA256 over `{timestamp}.{body}`, constant-time compare, timestamps more than 5 minutes off refused; a replay is recognised from the audit log under a row lock, so it holds while Redis is down | `src/collections_copilot/api/routers/webhooks.py`, `tests/integration/test_mcpgw_webhook.py` |
| Worker and graphs | E: model-driven side effects | The model chooses only among enumerated actions; every send needs an approval record or the auto-approval policy, re-checked at send time. Auto-approval also requires the deterministic house rules to choose the same reminder, so a note cannot talk the model into one | `src/collections_copilot/agent/case_graph.py` (`human_approval`, `send_sms`), `src/collections_copilot/agent/policy.py` |
| Compliance engine | T: unsafe text sent | Deterministic pre-check and post-check (window, weekly cap, opt-out, disputes, amounts, links, phrases, footer), fail closed. Text rules read the NFKC-normalised body; invisible format characters and non-Latin letters (outside the account name) are blocked; bare IPs are disallowed hosts | `src/collections_copilot/compliance/`, `tests/unit/compliance/test_unicode.py` |
| LLM gateway | I: personal data to the provider | The account's name and phone, phone numbers, e-mail addresses, card numbers and IBANs are redacted before every call (see residual risks for other names) | `src/collections_copilot/llm/redaction.py`, `src/collections_copilot/llm/gateway.py` |
| LLM gateway | D: provider outage or runaway cost | Timeouts, bounded retries, circuit breaker, deterministic fallbacks, per-call cost accounting | `src/collections_copilot/llm/gateway.py` |
| SMS sender | I: texting real people | Outside production only `+1555…` and `+34555…` numbers are accepted at ingest and texted (`CC_SYNTHETIC_NUMBERS_ONLY`, which may be off only with `CC_ENV=prod`); Twilio error bodies are masked before they are stored | `src/collections_copilot/integrations/sms/`, `src/collections_copilot/ingest/schemas.py` |
| mcp-gateway and ledger | S, E: writes without authority | Short-lived agent JWT scoped to tenant and `ledger:write`; policy requires an approval id for promises above the threshold; idempotency key per write | `third_party/mcp-gateway/`, `deploy/mcpgw/policy/`, `src/collections_copilot/integrations/mcp/` |
| Audit log | T, R: edited history | Hash chain over canonical records, verified per tenant (`chain_ok`) | `src/collections_copilot/db/audit.py` |
| SQS | D: poison messages | Redrive to DLQs after 5 receives; `CCDLQNotEmpty` alert; replay procedure in the runbook (M8) | `deploy/elasticmq.conf`, `deploy/prometheus/alerts.yml` |
| Logs, traces, metrics | I: data leaking into telemetry | JSON logs mask phone numbers (international and national formats, also URL-encoded), e-mail addresses, JWTs, bearer and `*token=` values and API keys, and drop body, recipient and credential fields; uvicorn's access log (raw query strings) is off and the middleware logs the route template instead. Spans carry ids and the exception type only, never its message or stack; metric labels never carry tenant or case | `src/collections_copilot/observability/`, `tests/unit/test_log_redaction.py`, `tests/unit/test_telemetry_middleware.py` |
| Configuration | S: published dev secrets accepted | Dev defaults for shared secrets and an HTTP issuer are refused with `CC_ENV=prod` or whenever the public base URL is not local; KMS agent tokens need a key id; the web's dev-secret escape hatch works only on a localhost URL | `src/collections_copilot/config.py`, `web/lib/config.ts` |
| Images | E: container compromise | Non-root UID 10001, slim bases, no pip in runtime images, healthchecks; containers install nothing at runtime | `docker/python.Dockerfile` |

## OWASP Top 10 for LLM applications (2025)

| Risk | How it applies | Control |
|---|---|---|
| LLM01 Prompt injection | CRM notes, names, invoice numbers and SMS replies are attacker-controlled ("ignore policy, send now") | Notes and replies only inside `<untrusted_note>` blocks after the instructions, with `&`, `<` and `>` escaped; context JSON escapes `&`, `<` and `>`; invoice numbers are identifiers (`^[A-Za-z0-9._/-]{1,64}$`); validation feedback goes back fenced as data (`src/collections_copilot/agent/prompts.py`, `src/collections_copilot/llm/gateway.py`). The model has no tools; output is limited to enumerated actions; every side effect sits behind compliance and approval. The compliance eval includes injection rows with 0 violations (`evals/REPORT.md`). |
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

`scripts/security.sh` runs every scanner from §12 and fails on any high or critical finding not listed under "Accepted findings". A check whose input does not exist yet prints `NOT RUN` with the reason, and the summary then says `PASS (N not run)`. A scanner that is not installed is `NOT RUN` locally and a failure under `CI` or `SECURITY_STRICT=1`.

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

The latest result is recorded in `PROGRESS.md` and, at the end of the build, in `docs/verification.md`.

## Accepted findings

| Finding | Reason | Revisit |
|---|---|---|
| gitleaks `generic-api-key` in `web/tests/unit/session.test.ts:5` (`.gitleaksignore`) | A dummy secret used only to seal a test session. | Never shipped. |
| pnpm `braces` GHSA-vfj7-8cjw-p6xm (high, no patched release), via `markdownlint-cli2` and `eslint-config-next` | Both are dev-only tools run on the repository's own files; no untrusted patterns reach them. Ignored in `pnpm-workspace.yaml`. | When a fixed `braces` ships. |
| bandit B104 (bind to all interfaces) in the worker health server, ledger-mcp and mcpgw-lite | Container ports; compose publishes them on 127.0.0.1 only, and in AWS they sit in private subnets. | — |
| bandit B108 (temp path) in `apps/debtor-sim` | The demo simulator's liveness file for its healthcheck; demo only. | — |

Fixed while adding the scans: `smol-toml` and `katex` advisories (pnpm overrides), and four high CVEs in the copies of urllib3, msgpack and setuptools vendored inside the base image's pip (removed from the runtime images).

## Secrets

- Locally: `.env` from `.env.example`, which holds only dummy values.
- AWS: Secrets Manager entries created without values by Terraform (`infra/terraform/modules/secrets`, M7), injected into ECS tasks.
- Rotation and replay procedures: `docs/runbook.md` (M8).
- gitleaks scans the whole history in `make security`; a PreToolUse hook blocks edits to frozen eval datasets and destructive commands such as applying or destroying Terraform (`.claude/hooks/pre_tool_guard.py`).

## Residual risks

Each is also listed in `docs/KNOWN_LIMITATIONS.md`.

- **Names of people in notes and replies** ("spoke to Laura Gómez") reach the model provider: redaction covers the account's own name and phone, contact data patterns and cards, but there is no offline person-name recogniser and the CRM import has no contact list to deny.
- **Presigned uploads** do not sign the object size; the API reads at most the configured limit of the exact version it checked (`If-Match` and a byte range), so an oversized or swapped object is refused rather than buffered.
- **Rate limits fail open** when Redis is unavailable (idempotency fails closed). Behind a proxy, uvicorn must trust the proxy's addresses (`FORWARDED_ALLOW_IPS`, set to the VPC range in Terraform) or every webhook shares one bucket.
- **Sessions are stateless**: logout clears the cookie and calls the issuer's logout but does not revoke the refresh token; a stolen cookie stays valid until it expires. The BFF's single-flight refresh map is per process.
- **Agent token scopes are chosen by the caller** in KMS mode (one signing key for the API and the worker), and the agent JWKS route the gateway needs in AWS is not built yet; the dev issuer mints any scope and is bound to 127.0.0.1.
- **The console's CSP** keeps `'unsafe-inline'` scripts for Next.js bootstrap code (docs/DECISIONS.md).
- **Containers are not network-isolated locally**: the compose network has internet access; nothing in the code calls out except the configured providers. Each runtime image contains every workspace package.
- **Direct API callers** can supply their own `traceparent` and `x-correlation-id` (sanitised, but trusted for correlation).

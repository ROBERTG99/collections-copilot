# Contributing

Thanks for looking at collections-copilot. This page covers setup, the workflow every change follows, the project conventions and how to add an eval case. The full specification is [docs/specs/000-build-spec.md](docs/specs/000-build-spec.md); [CLAUDE.md](CLAUDE.md) and [AGENTS.md](AGENTS.md) carry the same essentials for coding agents.

## Setup

Requirements: Linux or macOS, Docker with Compose v2, GNU make, Python 3.12 or later with [uv](https://docs.astral.sh/uv/), Node 22 with pnpm.

```bash
make bootstrap   # installs missing tools into ~/.local/bin, runs uv sync and pnpm install, creates .env from .env.example
make deps-up     # Postgres, Redis, ElasticMQ, MinIO, WireMock and Mailpit for host integration tests
make up          # or the full stack; then make seed
```

No API keys are needed. Everything runs offline with `CC_LLM_PROVIDER=fake`. Tool versions and environment notes are in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Workflow

1. **Write the failing test first.** Unit tests go in `tests/unit/`, tests against real dependencies in `tests/integration/`, provider contracts in `tests/contract/`, console tests in `web/tests/`. Run it and see it fail.
2. **Implement** until it passes.
3. **Run the fast loop:** `make check-fast` (ruff, import-linter, mypy `--strict`, unit and harness tests).
4. **Run the area's slower checks:** `make test` (unit, integration and contract tests with the coverage gates and the web unit tests), plus `make e2e`, `make eval`, `make bench` or `make tf-check` when you touched the console, prompts or agent code, performance-sensitive routes or `infra/`.
5. **Run the gate before a pull request:** `make verify` (lint, typecheck, test, security, tf-check, eval, bench). `make verify-clean` runs it in a fresh clone.
6. **Record what changed:** an ambiguous choice goes in [docs/DECISIONS.md](docs/DECISIONS.md) with the alternatives and the reason; an architectural one also gets an ADR in [docs/adr/](docs/adr/) (copy `0000-template.md` and name the rejected alternative); a gap you cannot close goes in [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md).

Never skip, mark as expected-failure or weaken a test, and never lower an eval threshold to get green. Fix the code, or record the gap.

## Conventions

From [CLAUDE.md](CLAUDE.md) and `.claude/rules/`:

- **Async I/O everywhere:** FastAPI, SQLAlchemy 2 async with psycopg 3, LangGraph with `AsyncPostgresSaver` (`thread_id = case_id`).
- **Tenancy:** every table has `tenant_id`. Read and write data only through `collections_copilot.db.repositories`, whose methods take the tenant as their first argument. Raw SQL is banned outside `collections_copilot.db` by a ruff rule.
- **Money** is `bigint` cents plus a `currency` code; never floats. **Times** are timezone-aware (`timestamptz`); debtor-local times use `zoneinfo`.
- **Deterministic code owns decisions with side effects:** the priority score, compliance, amounts, STOP handling and every send or write permission. The LLM only reads notes and replies and drafts text.
- **LLM code:** only `collections_copilot.llm` imports provider SDKs (import-linter checks it). Model IDs come from `CC_MODEL_FAST` and `CC_MODEL_REASONING`. Prompts are versioned files `prompts/<name>.v<N>.md`; untrusted text appears only inside `<untrusted_note>` blocks after the instructions. Every output is validated against its schema, with one retry and then the deterministic fallback.
- **API:** errors are RFC 9457 problem details with a `correlation_id`; mutating routes accept `Idempotency-Key`. After an API change, run `make gen-types` to regenerate the console's types.
- **Settings** come from `collections_copilot.config.Settings` (`CC_*`); document every new variable in `.env.example`.
- **Logs** are JSON through `collections_copilot.observability.logging`; never log phone numbers, message bodies or tokens.
- **Tests:** no sleeps (poll with a timeout, `tests/helpers.py::eventually`), clocks frozen with `time-machine`, fixed seeds, one tenant per integration test, no network in unit tests, no live model calls in any test.
- **Console:** every screen has loading, empty, error and success states; WCAG 2.2 AA with axe in every Playwright spec; the browser never holds tokens; model output carries the "AI draft" chip and fallbacks say "Template".
- **Docker:** non-root users (UID 10001), a healthcheck in every image and service, host ports in 8300–8399.
- **Terraform:** never run apply or destroy; no wildcard IAM actions outside the documented exceptions; KMS everywhere; validated variables; `make tf-check` before committing.
- **Docs:** every reported number cites the command or report that produced it (`tests/harness/test_docs.py` checks this); English only; `pnpm run lint:md` must be clean.

## Commits and pull requests

- Use [Conventional Commits](https://www.conventionalcommits.org/): `feat(scope): …`, `fix(scope): …`, `test: …`, `docs: …`, `chore: …`, `ci: …`. One logical change per commit, with the checks green.
- Commits go out under the author's own identity. Do not add `Co-Authored-By`, `Claude-Session` or "Generated with Claude Code" trailers to commits or pull requests, and do not change `git config user.*` or the `GIT_AUTHOR_*` / `GIT_COMMITTER_*` variables. The repository's agent hook blocks both.
- Fill in the pull request template: what and why, how it was verified (commands and one-line results), and the checklist.
- Never force-push a shared branch and never push tags.

## Synthetic data only

- Phone numbers only in the reserved `+1555…` and `+34555…` ranges; e-mail addresses on `example` domains.
- Data comes from the generator in `data/generator/` (gen.v1, seed 4242). Never commit real personal data, real company names of customers or debtors, or secrets; `.env.example` holds only local dummy values.
- Never send a message to a real recipient. Locally, SMS goes to WireMock and e-mail to Mailpit.
- Every side effect (SMS, ledger write, e-mail) must pass the compliance engine and an approval record or the auto-approval policy. Webhooks must be verified and deduplicated.

## Adding an eval case

Eval datasets are frozen once committed: `evals/datasets/*.v1.jsonl` and their digests in `evals/datasets/MANIFEST.json` are never edited, and the agent hook blocks edits to them. The `add-eval-case` skill (`.claude/skills/add-eval-case/SKILL.md`) describes the steps:

1. Extend `data/generator/evals.py` with the new scenario. Labels are set by construction, from how the row is built, never by a model.
2. Write a new dataset version (`*.v2.jsonl`) instead of editing a committed `*.v1.jsonl` file, and update `evals/datasets/MANIFEST.json`.
3. Point the suite in `evals/suites/` at the new version and run `make eval`.
4. Tune prompts, rules or the fake only on the `dev` split; report on `test`. Thresholds only rise.

See [docs/evaluation.md](docs/evaluation.md) for what each suite measures and how the gate works. `evals/REPORT.md` is generated by `make eval`; never edit it by hand.

## Reporting security issues

Do not open a public issue for a vulnerability. Follow [SECURITY.md](SECURITY.md); the threat model and accepted findings are in [docs/security.md](docs/security.md).

## Licence

By contributing you agree that your contribution is licensed under the [MIT licence](LICENSE).

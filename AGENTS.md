# AGENTS.md — collections-copilot

Instructions for AI coding assistants working in this repository. `CLAUDE.md` holds the same essentials with more detail; the architecture is in `docs/architecture.md`.

## What this is

A B2B collections assistant. Deterministic code computes the priority score, enforces compliance (contact window 08:00–21:00 debtor local time, ≤ 3 contacts per 7 days, opt-outs, disputes, ledger-only amounts, footer, forbidden phrases, link allowlist), handles STOP and gates every side effect. An LLM (Claude via Bedrock or the Anthropic API; `FakeLLM` offline) reads notes and replies and drafts SMS. A human approves every send unless the auto-approval policy matches.

## Commands

- Setup: `make bootstrap`
- Fast loop: `make check-fast`
- Full tests: `make test` (needs Docker for Postgres, Redis, ElasticMQ, MinIO, WireMock, Mailpit)
- Stack: `make up`, `make seed`, `make demo`
- Gate: `make verify`

## Rules

1. Write the failing test first; never weaken tests or thresholds.
2. Tenant-scoped repositories only; money in cents; timezone-aware datetimes.
3. Provider SDKs only in `src/collections_copilot/llm/`.
4. Synthetic data only (`+1555…` / `+34555…` numbers).
5. No attribution trailers in commits or PRs; never change git identity.
6. Never run `terraform apply`/`destroy` or deploy anywhere.
7. Record design decisions in `docs/DECISIONS.md` and known gaps in `docs/KNOWN_LIMITATIONS.md`.

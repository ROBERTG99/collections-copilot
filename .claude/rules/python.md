---
paths:
  - "src/**/*.py"
  - "apps/**/*.py"
  - "tests/**/*.py"
  - "migrations/**/*.py"
---
# Python conventions

- Python ≥ 3.12, async everywhere on I/O paths (FastAPI, SQLAlchemy async, aiobotocore, httpx).
- Every table row carries `tenant_id`; read and write it only through `collections_copilot.db.repositories`, which take the tenant as their first argument. Raw SQL (`sqlalchemy.text`) is banned outside `collections_copilot.db` by a ruff rule.
- Money is integer cents plus a `currency` code. Never use floats for money; format only at the edge.
- Times are timezone-aware (`datetime.now(UTC)`); debtor-local times use `zoneinfo`.
- Settings come from `collections_copilot.config.Settings` (`CC_*`); document every new variable in `.env.example`.
- `mypy --strict` must pass on `src/` and `apps/`; `ruff check` and `ruff format` must be clean.
- Logs are JSON via `collections_copilot.observability.logging`; never log phone numbers, message bodies or tokens.

---
name: add-migration
description: "Add an Alembic migration with tenant_id, cents and timestamptz conventions and test upgrade/downgrade."
---
# Add a migration

1. Change `src/collections_copilot/db/models.py` (every table: `tenant_id uuid not null`; money `bigint` cents + `currency char(3)`; `timestamptz`).
2. `uv run alembic revision -m "<summary>"` and write explicit `upgrade()`/`downgrade()` (no autogenerate surprises).
3. Extend `tests/integration/db/test_migrations.py` with the new constraints.
4. `uv run alembic upgrade head && uv run alembic downgrade base && uv run alembic upgrade head`.
5. Add repository methods in `db/repositories/` (tenant first argument).

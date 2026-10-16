"""Alembic environment: runs migrations synchronously with psycopg 3 against CC_DATABASE_URL."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config
target_metadata = None


def _url() -> str:
    url = config.get_main_option("sqlalchemy.url") or os.environ.get(
        "CC_DATABASE_URL", "postgresql+psycopg://cc:cc@localhost:8320/cc"
    )
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg://")


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

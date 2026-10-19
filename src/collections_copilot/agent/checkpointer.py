"""LangGraph AsyncPostgresSaver in schema `checkpoints` (thread_id = case_id)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


def libpq_url(sqlalchemy_url: str) -> str:
    """Turn `postgresql+psycopg://` into a libpq URL."""
    return sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)


async def _configure(conn: AsyncConnection) -> None:
    await conn.execute("SET search_path TO checkpoints")


@asynccontextmanager
async def open_checkpointer(
    database_url: str, max_size: int = 20
) -> AsyncIterator[AsyncPostgresSaver]:
    url = libpq_url(database_url)
    async with await AsyncConnection.connect(url, autocommit=True) as conn:
        await conn.execute("CREATE SCHEMA IF NOT EXISTS checkpoints")
    pool: AsyncConnectionPool[AsyncConnection] = AsyncConnectionPool(
        url,
        min_size=1,
        max_size=max_size,
        open=False,
        configure=_configure,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open()
    try:
        saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
        async with await AsyncConnection.connect(url, autocommit=True) as lock:
            await lock.execute("SELECT pg_advisory_lock(424202)")
            try:
                await saver.setup()
            finally:
                await lock.execute("SELECT pg_advisory_unlock(424202)")
        yield saver
    finally:
        await pool.close()

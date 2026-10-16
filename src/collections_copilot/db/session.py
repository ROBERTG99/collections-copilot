"""Async SQLAlchemy engine and session factory (psycopg 3)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(url: str, pool_size: int = 10) -> AsyncEngine:
    return create_async_engine(url, pool_size=pool_size, max_overflow=10, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def transaction(sm: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """One unit of work: commit on success, roll back on error."""
    async with sm() as session, session.begin():
        yield session


async def ping(engine: AsyncEngine) -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@asynccontextmanager
async def advisory_lock(sm: async_sessionmaker[AsyncSession], key: str) -> AsyncIterator[None]:
    """Hold a Postgres advisory lock on `key` for the block (one holder across all workers).

    The lock lives on its own connection inside a transaction (`pg_advisory_xact_lock`), so it is
    released when the block ends or the connection dies with the worker."""
    async with sm() as session, session.begin():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
        )
        yield


class LeaderLock:
    """A session-level advisory lock held on one dedicated connection: exactly one holder across
    all workers. If the holder dies, Postgres drops the connection and the lock with it."""

    def __init__(self, engine: AsyncEngine, key: str) -> None:
        self.engine = engine
        self.key = key
        self._conn: AsyncConnection | None = None

    @property
    def held(self) -> bool:
        return self._conn is not None

    async def try_acquire(self) -> bool:
        if self._conn is not None:
            try:  # still alive? A dropped connection means the lock is gone.
                await self._conn.execute(text("SELECT 1"))
                return True
            except Exception:
                await self._close()
        conn = await self.engine.connect()
        try:
            got = (
                await conn.execute(
                    text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                    {"key": self.key},
                )
            ).scalar_one()
            await conn.commit()
        except Exception:
            await conn.close()
            raise
        if got:
            self._conn = conn
            return True
        await conn.close()
        return False

    async def release(self) -> None:
        if self._conn is None:
            return
        try:
            await self._conn.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), {"key": self.key}
            )
            await self._conn.commit()
        finally:
            await self._close()

    async def _close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            await conn.close()

"""Shared fixtures. Integration tests run against the compose dependencies (`make deps-up`)."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]
PG_ADMIN_URL = os.environ.get("CC_TEST_PG_ADMIN_URL", "postgresql://cc:cc@localhost:8320/cc")


def _db_url(name: str) -> str:
    base, _, _ = PG_ADMIN_URL.rpartition("/")
    return f"{base}/{name}"


def alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("postgresql://", "postgresql+psycopg://", 1))
    return cfg


def create_database() -> str:
    name = f"cc_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(PG_ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    return name


def drop_database(name: str) -> None:
    with psycopg.connect(PG_ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def empty_database() -> Iterator[str]:
    """A fresh, unmigrated database; yields its libpq URL."""
    name = create_database()
    try:
        yield _db_url(name)
    finally:
        drop_database(name)


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[str]:
    """One migrated database per test session; tests isolate themselves with a fresh tenant."""
    name = create_database()
    url = _db_url(name)
    command.upgrade(alembic_config(url), "head")
    try:
        yield url
    finally:
        drop_database(name)


@pytest.fixture(scope="session")
def database_url(migrated_database: str) -> str:
    """SQLAlchemy async URL of the session database."""
    return migrated_database.replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture
async def sessionmaker(database_url: str) -> AsyncIterator[object]:
    from collections_copilot.db.session import make_engine, make_sessionmaker

    engine = make_engine(database_url, pool_size=5)
    try:
        yield make_sessionmaker(engine)
    finally:
        await engine.dispose()

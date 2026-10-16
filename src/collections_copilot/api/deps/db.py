"""Unit-of-work dependency: one transaction per request, committed on success."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request

from collections_copilot.db.repositories import Repos


async def get_repos(request: Request) -> AsyncIterator[Repos]:
    async with request.app.state.sessionmaker() as session, session.begin():
        yield Repos(session)

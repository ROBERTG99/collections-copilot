"""Unit-of-work dependency: one transaction per request, committed on success."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Request

from collections_copilot.db.repositories import Repos


async def get_repos(request: Request) -> AsyncIterator[Repos]:
    async with request.app.state.sessionmaker() as session, session.begin():
        yield Repos(session)


# Commit before the response is sent (scope="function"), so a 2xx always means committed.
REPOS = Depends(get_repos, scope="function")

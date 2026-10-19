"""Repository base: every query is filtered by tenant_id (the first argument of every method)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

TenantId = uuid.UUID


class Repo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session


def as_uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None

from __future__ import annotations

import uuid

from sqlalchemy import select

from collections_copilot.db.models import Tenant
from collections_copilot.db.repositories.base import Repo


class TenantsRepo(Repo):
    """Tenants are the scoping root; lookups here are by id or name only."""

    async def get(self, tenant_id: uuid.UUID) -> Tenant | None:
        return await self.s.get(Tenant, tenant_id)

    async def get_by_name(self, name: str) -> Tenant | None:
        return (
            await self.s.execute(select(Tenant).where(Tenant.name == name))
        ).scalar_one_or_none()

    async def ensure(self, tenant_id: uuid.UUID, name: str) -> Tenant:
        existing = await self.get(tenant_id)
        if existing is not None:
            return existing
        tenant = Tenant(id=tenant_id, name=name)
        self.s.add(tenant)
        await self.s.flush()
        return tenant

    async def list_ids(self) -> list[uuid.UUID]:
        return list((await self.s.execute(select(Tenant.id))).scalars())

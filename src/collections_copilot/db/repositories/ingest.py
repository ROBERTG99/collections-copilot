from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select

from collections_copilot.db.models import IngestBatch
from collections_copilot.db.repositories.base import Repo, TenantId, as_uuid


class IngestRepo(Repo):
    async def create(self, tenant_id: TenantId, **fields: Any) -> IngestBatch:
        batch = IngestBatch(tenant_id=tenant_id, **fields)
        self.s.add(batch)
        await self.s.flush()
        return batch

    async def get(self, tenant_id: TenantId, batch_id: uuid.UUID | str) -> IngestBatch | None:
        bid = as_uuid(batch_id)
        if bid is None:
            return None
        stmt = select(IngestBatch).where(IngestBatch.tenant_id == tenant_id, IngestBatch.id == bid)
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def by_sha(self, tenant_id: TenantId, sha256: str) -> IngestBatch | None:
        stmt = select(IngestBatch).where(
            IngestBatch.tenant_id == tenant_id, IngestBatch.sha256 == sha256
        )
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def delete(self, tenant_id: TenantId, batch_id: uuid.UUID) -> None:
        await self.s.execute(
            delete(IngestBatch).where(
                IngestBatch.tenant_id == tenant_id, IngestBatch.id == batch_id
            )
        )

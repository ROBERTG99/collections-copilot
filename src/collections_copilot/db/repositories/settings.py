from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from collections_copilot.db.models import Setting
from collections_copilot.db.repositories.base import Repo, TenantId


class SettingsRepo(Repo):
    async def get(self, tenant_id: TenantId, key: str) -> dict[str, Any] | None:
        stmt = select(Setting.value).where(Setting.tenant_id == tenant_id, Setting.key == key)
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def put(
        self, tenant_id: TenantId, key: str, value: dict[str, Any], updated_by: str
    ) -> None:
        stmt = insert(Setting).values(
            tenant_id=tenant_id, key=key, value=value, updated_by=updated_by
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "key"],
            set_={
                "value": stmt.excluded.value,
                "updated_by": updated_by,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await self.s.execute(stmt)

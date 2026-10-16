"""GET /me: the signed-in principal (roles from cognito:groups, tenant from custom:tenant_id)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from collections_copilot.api.deps.auth import ANY_ROLE, Principal

router = APIRouter(prefix="/api/v1", tags=["me"])


class MeOut(BaseModel):
    sub: str
    username: str
    tenant_id: uuid.UUID
    roles: list[str]


@router.get("/me", response_model=MeOut)
async def me(principal: Principal = Depends(ANY_ROLE)) -> MeOut:
    return MeOut(
        sub=principal.sub,
        username=principal.username,
        tenant_id=principal.tenant_id,
        roles=sorted(principal.roles),
    )

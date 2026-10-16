"""Auto-approval settings (FR-6): supervisors may only tighten the environment ceiling."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from collections_copilot.agent import policy
from collections_copilot.api.deps.auth import ANY_ROLE, SUPERVISOR, Principal
from collections_copilot.api.deps.db import REPOS
from collections_copilot.api.errors import ProblemError
from collections_copilot.db import audit
from collections_copilot.db.repositories import Repos

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])
KEY = "auto_approval"


class AutoApprovalIn(BaseModel):
    enabled: bool
    max_usd: float = Field(ge=0, le=1_000_000)
    min_confidence: float = Field(ge=0, le=1)


class AutoApprovalOut(BaseModel):
    enabled: bool
    max_usd: float
    min_confidence: float
    ceiling_max_usd: float
    floor_min_confidence: float
    actions: list[str] = ["remind"]


def _out(request: Request, setting: dict[str, object] | None) -> AutoApprovalOut:
    s = request.app.state.settings
    eff = policy.effective(setting, s.auto_approve_max_usd, s.auto_approve_min_confidence)
    return AutoApprovalOut(
        enabled=eff.enabled,
        max_usd=eff.max_usd,
        min_confidence=eff.min_confidence,
        ceiling_max_usd=s.auto_approve_max_usd,
        floor_min_confidence=s.auto_approve_min_confidence,
    )


@router.get("/auto-approval", response_model=AutoApprovalOut)
async def get_auto_approval(
    request: Request, principal: Principal = Depends(ANY_ROLE), repos: Repos = REPOS
) -> AutoApprovalOut:
    return _out(request, await repos.settings.get(principal.tenant_id, KEY))


@router.put("/auto-approval", response_model=AutoApprovalOut, responses={422: {}})
async def put_auto_approval(
    body: AutoApprovalIn,
    request: Request,
    principal: Principal = Depends(SUPERVISOR),
    repos: Repos = REPOS,
) -> AutoApprovalOut:
    s = request.app.state.settings
    value = body.model_dump()
    if not policy.is_tightening(value, s.auto_approve_max_usd, s.auto_approve_min_confidence):
        raise ProblemError(
            422,
            f"settings may only tighten the ceiling (max_usd ≤ {s.auto_approve_max_usd},"
            f" min_confidence ≥ {s.auto_approve_min_confidence})",
            code="loosens_policy",
        )
    before = await repos.settings.get(principal.tenant_id, KEY)
    await repos.settings.put(principal.tenant_id, KEY, value, principal.username)
    await audit.append(
        repos.session,
        principal.tenant_id,
        actor_type="user",
        actor_id=principal.username,
        action="settings.auto_approval",
        entity="setting",
        entity_id=KEY,
        payload={"before": before, "after": value},
    )
    return _out(request, value)

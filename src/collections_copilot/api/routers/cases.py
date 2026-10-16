"""Cases endpoints (walking skeleton; T-26 adds persistence, RBAC and tenant scoping)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from collections_copilot.integrations.queue.messages import TaskMessage

router = APIRouter(prefix="/api/v1", tags=["cases"])
SKELETON_TENANT = "00000000-0000-0000-0000-000000000001"


class CreateCases(BaseModel):
    account_ids: list[str] = Field(min_length=1, max_length=400)


@router.post("/cases", status_code=202)
async def create_cases(body: CreateCases, request: Request) -> dict[str, Any]:
    app = request.app
    cases = []
    for account_id in body.account_ids:
        case_id = str(uuid.uuid4())
        task = TaskMessage(tenant_id=SKELETON_TENANT, kind="process_case", case_id=case_id)
        await app.state.queues.send(
            app.state.settings.sqs_tasks_queue, task.model_dump(mode="json", by_alias=True)
        )
        cases.append({"id": case_id, "account_id": account_id, "status": "queued"})
    return {"cases": cases}


@router.get("/cases/{case_id}")
async def get_case(case_id: str, request: Request) -> JSONResponse:
    graph = request.app.state.graph
    snapshot = await graph.aget_state({"configurable": {"thread_id": case_id}})
    if not snapshot.values:
        return JSONResponse({"status": "queued", "id": case_id})
    status = (
        "awaiting_approval" if "human_approval" in snapshot.next else snapshot.values.get("status")
    )
    return JSONResponse({"id": case_id, "status": status, "draft": snapshot.values.get("draft")})

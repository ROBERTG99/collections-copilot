"""Queue message contracts: cc.task.v1 and cc.reply.v1."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

TaskKind = Literal["process_case", "resume_case", "follow_up", "retry_write_back"]


class TaskMessage(BaseModel):
    schema_: Literal["cc.task.v1"] = Field(default="cc.task.v1", alias="schema")
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str
    kind: TaskKind
    case_id: str | None = None
    resume: dict[str, Any] | None = None
    attempt: int = 0
    enqueued_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    trace_context: dict[str, str] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class ReplyMessage(BaseModel):
    schema_: Literal["cc.reply.v1"] = Field(default="cc.reply.v1", alias="schema")
    tenant_id: str
    message_id: str
    provider_sid: str
    from_e164: str
    body: str
    received_at: datetime
    trace_context: dict[str, str] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}

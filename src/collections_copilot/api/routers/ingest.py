"""Ingest endpoints: JSON aging rows and presigned S3 CSV uploads (FR-1, NFR-9)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from collections_copilot.api.deps.auth import ANY_ROLE, Principal
from collections_copilot.api.deps.db import get_repos
from collections_copilot.api.errors import ProblemError
from collections_copilot.db.models import IngestBatch
from collections_copilot.db.repositories import Repos
from collections_copilot.ingest.service import (
    TooManyRowsError,
    ingest,
    parse_csv,
    sha256_bytes,
    sha256_rows,
    validate_rows,
)
from collections_copilot.integrations.storage.s3 import ObjectNotFoundError, ObjectTooLargeError

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])


class UploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255, pattern=r"^[\w.\- ]+\.csv$")
    size_bytes: int = Field(gt=0)


class UploadResponse(BaseModel):
    upload_id: uuid.UUID
    put_url: str
    expires_in: int = 900


class BatchRef(BaseModel):
    batch_id: uuid.UUID
    duplicate: bool
    status: str


class AgingRequest(BaseModel):
    rows: list[dict[str, Any]] = Field(min_length=1)


class BatchOut(BaseModel):
    id: uuid.UUID
    status: str
    source: str
    row_count: int
    filename: str | None
    errors: dict[str, Any]
    created_at: datetime


def _batch_out(b: IngestBatch) -> BatchOut:
    return BatchOut(
        id=b.id,
        status=b.status,
        source=b.source,
        row_count=b.row_count,
        filename=b.filename,
        errors=b.errors or {},
        created_at=b.created_at,
    )


@router.post("/uploads", response_model=UploadResponse, status_code=201)
async def create_upload(
    body: UploadRequest,
    request: Request,
    principal: Principal = Depends(ANY_ROLE),
    repos: Repos = Depends(get_repos),
) -> UploadResponse:
    settings = request.app.state.settings
    if body.size_bytes > settings.upload_max_bytes:
        raise ProblemError(
            413, f"uploads are limited to {settings.upload_max_bytes} bytes", code="too_large"
        )
    batch = await repos.ingest.create(
        principal.tenant_id,
        source="upload",
        status="awaiting_upload",
        created_by=principal.username,
        filename=body.filename,
        size_bytes=body.size_bytes,
    )
    key = f"uploads/{principal.tenant_id}/{batch.id}/{body.filename.replace(' ', '_')}"
    batch.object_key = key
    put_url = await request.app.state.object_store.presign_put(key, "text/csv", 900)
    return UploadResponse(upload_id=batch.id, put_url=put_url)


@router.post("/uploads/{upload_id}:complete", response_model=BatchRef)
async def complete_upload(
    upload_id: str,
    request: Request,
    principal: Principal = Depends(ANY_ROLE),
    repos: Repos = Depends(get_repos),
) -> BatchRef:
    settings = request.app.state.settings
    batch = await repos.ingest.get(principal.tenant_id, upload_id)
    if batch is None or batch.object_key is None:
        raise ProblemError(404, "upload not found", code="not_found")
    if batch.status != "awaiting_upload":
        return BatchRef(batch_id=batch.id, duplicate=False, status=batch.status)
    try:
        data = await request.app.state.object_store.get_bytes(
            batch.object_key, settings.upload_max_bytes
        )
    except ObjectTooLargeError as exc:
        raise ProblemError(413, "uploaded object exceeds the size limit", code="too_large") from exc
    except ObjectNotFoundError as exc:
        raise ProblemError(404, "object was not uploaded", code="not_uploaded") from exc
    try:
        parsed = parse_csv(data, settings.upload_max_rows)
    except TooManyRowsError as exc:
        raise ProblemError(413, str(exc), code="too_many_rows") from exc
    result, duplicate = await ingest(
        repos,
        principal.tenant_id,
        sha256=sha256_bytes(data),
        parsed=parsed,
        source="upload",
        actor=principal.username,
        object_key=batch.object_key,
        size_bytes=len(data),
        batch=batch,
    )
    return BatchRef(batch_id=result.id, duplicate=duplicate, status=result.status)


@router.post("/aging", response_model=BatchRef)
async def ingest_aging(
    body: AgingRequest,
    request: Request,
    principal: Principal = Depends(ANY_ROLE),
    repos: Repos = Depends(get_repos),
) -> BatchRef:
    settings = request.app.state.settings
    try:
        parsed = validate_rows(body.rows, settings.upload_max_rows)
    except TooManyRowsError as exc:
        raise ProblemError(413, str(exc), code="too_many_rows") from exc
    result, duplicate = await ingest(
        repos,
        principal.tenant_id,
        sha256=sha256_rows(body.rows),
        parsed=parsed,
        source="json",
        actor=principal.username,
    )
    return BatchRef(batch_id=result.id, duplicate=duplicate, status=result.status)


@router.get("/batches/{batch_id}", response_model=BatchOut)
async def get_batch(
    batch_id: str, principal: Principal = Depends(ANY_ROLE), repos: Repos = Depends(get_repos)
) -> BatchOut:
    batch = await repos.ingest.get(principal.tenant_id, batch_id)
    if batch is None:
        raise ProblemError(404, "batch not found", code="not_found")
    return _batch_out(batch)

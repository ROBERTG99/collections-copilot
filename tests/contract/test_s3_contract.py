"""ObjectStore contract: the S3 adapter behaves the same against MinIO and moto."""

from __future__ import annotations

import uuid

import httpx
import pytest

from collections_copilot.integrations.storage.s3 import (
    ObjectNotFoundError,
    ObjectTooLargeError,
    S3ObjectStore,
)

pytestmark = pytest.mark.contract


@pytest.fixture(params=["minio", "moto"])
def endpoint(request: pytest.FixtureRequest, moto_url: str) -> str:
    if request.param == "minio":
        return "http://localhost:8341"
    return moto_url


async def test_presigned_put_then_read(endpoint: str) -> None:
    async with S3ObjectStore(f"contract-{uuid.uuid4().hex[:8]}", "us-east-1", endpoint) as store:
        await store.ensure_bucket()
        await store.ensure_bucket()  # idempotent
        url = await store.presign_put("uploads/a.csv", "text/csv", 60)
        async with httpx.AsyncClient() as client:
            resp = await client.put(
                url, content=b"a,b\n1,2\n", headers={"Content-Type": "text/csv"}
            )
        assert resp.status_code == 200
        assert await store.get_bytes("uploads/a.csv", 1024) == b"a,b\n1,2\n"
        with pytest.raises(ObjectTooLargeError):
            await store.get_bytes("uploads/a.csv", 3)
        with pytest.raises(ObjectNotFoundError):
            await store.get_bytes("uploads/missing.csv", 1024)
        await store.put_bytes("x.csv", b"x")
        await store.delete("x.csv")
        with pytest.raises(ObjectNotFoundError):
            await store.get_bytes("x.csv", 10)

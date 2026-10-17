"""ObjectStore interface and the S3 adapter (aiobotocore; MinIO locally, moto in tests)."""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any, Protocol

from aiobotocore.config import AioConfig
from aiobotocore.session import get_session


class ObjectTooLargeError(Exception):
    pass


class ObjectNotFoundError(Exception):
    pass


class ObjectStore(Protocol):
    async def ensure_bucket(self) -> None: ...

    async def presign_put(self, key: str, content_type: str, expires_s: int = 900) -> str: ...

    async def put_bytes(self, key: str, data: bytes, content_type: str = "text/csv") -> None: ...

    async def get_bytes(self, key: str, max_bytes: int) -> bytes: ...

    async def delete(self, key: str) -> None: ...


class S3ObjectStore:
    def __init__(
        self,
        bucket: str,
        region: str,
        endpoint_url: str | None = None,
        public_endpoint_url: str | None = None,
    ) -> None:
        self.bucket = bucket
        self.region = region
        self.endpoint_url = endpoint_url
        self.public_endpoint_url = public_endpoint_url or endpoint_url
        self._stack = AsyncExitStack()
        self._client: Any = None
        self._presigner: Any = None

    async def __aenter__(self) -> S3ObjectStore:
        session = get_session()
        cfg = AioConfig(signature_version="s3v4", s3={"addressing_style": "path"})
        self._client = await self._stack.enter_async_context(
            session.create_client(
                "s3", region_name=self.region, endpoint_url=self.endpoint_url, config=cfg
            )
        )
        # Presigned URLs must use the host the browser can reach (differs inside Docker).
        self._presigner = await self._stack.enter_async_context(
            session.create_client(
                "s3", region_name=self.region, endpoint_url=self.public_endpoint_url, config=cfg
            )
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()

    async def ensure_bucket(self) -> None:
        try:
            await self._client.head_bucket(Bucket=self.bucket)
        except Exception:
            kwargs: dict[str, Any] = {"Bucket": self.bucket}
            if self.region != "us-east-1":
                kwargs["CreateBucketConfiguration"] = {"LocationConstraint": self.region}
            await self._client.create_bucket(**kwargs)

    async def presign_put(self, key: str, content_type: str, expires_s: int = 900) -> str:
        url: str = await self._presigner.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires_s,
        )
        return url

    async def put_bytes(self, key: str, data: bytes, content_type: str = "text/csv") -> None:
        await self._client.put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
        )

    async def get_bytes(self, key: str, max_bytes: int) -> bytes:
        try:
            head = await self._client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise ObjectNotFoundError(key) from exc
        if int(head["ContentLength"]) > max_bytes:
            raise ObjectTooLargeError(key)
        # The presigned URL stays valid after the HEAD: read the same version (ETag) and never
        # more than max_bytes + 1, so a swapped-in larger object cannot exhaust memory.
        try:
            obj = await self._client.get_object(
                Bucket=self.bucket,
                Key=key,
                IfMatch=head["ETag"],
                Range=f"bytes=0-{max_bytes}",
            )
        except Exception as exc:
            raise ObjectTooLargeError(key) from exc
        async with obj["Body"] as stream:
            data: bytes = await stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ObjectTooLargeError(key)
        return data

    async def delete(self, key: str) -> None:
        await self._client.delete_object(Bucket=self.bucket, Key=key)

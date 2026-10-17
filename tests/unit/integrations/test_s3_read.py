"""get_bytes reads the body to the end: one read() may return fewer bytes than asked for."""

from __future__ import annotations

import pytest

from collections_copilot.integrations.storage.s3 import ObjectTooLargeError, read_capped


class ChunkedBody:
    def __init__(self, data: bytes, chunk: int) -> None:
        self.data, self.chunk, self.pos = data, chunk, 0

    async def read(self, n: int = -1) -> bytes:
        size = self.chunk if n < 0 else min(n, self.chunk)
        out = self.data[self.pos : self.pos + size]
        self.pos += len(out)
        return out


async def test_reads_every_chunk() -> None:
    data = b"a,b\n" * 10_000
    assert await read_capped(ChunkedBody(data, 7), len(data)) == data


async def test_stops_once_over_the_cap() -> None:
    body = ChunkedBody(b"x" * 1000, 64)
    with pytest.raises(ObjectTooLargeError):
        await read_capped(body, 100)
    assert body.pos <= 100 + 64  # never reads far past the cap


async def test_empty_body() -> None:
    assert await read_capped(ChunkedBody(b"", 8), 10) == b""

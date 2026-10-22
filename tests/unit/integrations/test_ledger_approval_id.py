"""The gateway's approval id goes into admin URL paths later, so a
malformed one is refused when the ledger response is parsed, not with a 500 at approval time."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from collections_copilot.integrations.mcp.ledger import McpLedgerTools


class Canned(McpLedgerTools):
    def __init__(self, approval_id: str) -> None:
        super().__init__("http://gateway.test", tokens=None)  # type: ignore[arg-type]
        self.approval_id = approval_id

    async def _call(self, *args: Any) -> Any:
        data = {"status": "approval_required", "approval_id": self.approval_id, "reasons": []}
        return SimpleNamespace(structured_content=data, is_error=True)


@pytest.mark.parametrize(
    ("approval_id", "status"),
    [("apr_1-2", "approval_required"), ("../x", "error"), ("a:approve", "error")],
)
async def test_only_plain_approval_ids_are_held(approval_id: str, status: str) -> None:
    result = await Canned(approval_id).record_promise_to_pay("t", {}, idempotency_key="k")
    assert result.status == status

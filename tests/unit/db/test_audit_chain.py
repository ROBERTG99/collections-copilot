"""The audit hash chain detects tampering at the first bad sequence number."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from collections_copilot.db.audit import AuditRecord, chain, verify_chain

TENANT = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


def _records(n: int) -> list[AuditRecord]:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    raw = [
        AuditRecord(
            seq=i + 1,
            tenant_id=TENANT,
            ts=t0 + timedelta(seconds=i),
            actor_type="user",
            actor_id="ana.analyst",
            action="approval.approve",
            entity="case",
            entity_id=str(i),
            payload_redacted={"i": i, "decision": "approve"},
        )
        for i in range(n)
    ]
    return chain(raw)


def test_valid_chain_verifies() -> None:
    assert verify_chain(_records(10)) is None


def test_tamper_detected_at_first_bad_seq() -> None:
    rows = _records(10)
    rows[4] = rows[4].model_copy(update={"actor_id": "mallory"})
    rows[7] = rows[7].model_copy(update={"action": "approval.reject"})
    assert verify_chain(rows) == 5


def test_deleted_row_detected() -> None:
    rows = _records(6)
    del rows[2]
    assert verify_chain(rows) == 4


@settings(max_examples=60, deadline=None)
@given(idx=st.integers(min_value=0, max_value=7), pos=st.integers(min_value=0, max_value=200))
def test_any_single_byte_change_is_detected(idx: int, pos: int) -> None:
    rows = _records(8)
    target = rows[idx]
    text = target.actor_id + "|" + target.action + "|" + str(target.payload_redacted)
    pos %= len(target.actor_id)
    changed = (
        target.actor_id[:pos]
        + chr((ord(target.actor_id[pos]) + 1) % 128 or 1)
        + target.actor_id[pos + 1 :]
    )
    assert text  # the record has content to tamper with
    rows[idx] = target.model_copy(update={"actor_id": changed})
    assert verify_chain(rows) == target.seq

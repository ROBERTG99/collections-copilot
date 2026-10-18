"""follow-up kind promise_write_back

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-18 16:00:00.000000

A promise whose ledger write hit an outage is retried by the scheduler (T-51 reviews).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = (
    "kind IN ('after_send', 'deferred_send', 'promise_due', 'approval_reminder',"
    " 'payment_verification', 'send_retry')"
)
NEW = (
    "kind IN ('after_send', 'deferred_send', 'promise_due', 'approval_reminder',"
    " 'payment_verification', 'send_retry', 'promise_write_back')"
)


def upgrade() -> None:
    op.drop_constraint("ck_follow_ups_kind", "follow_ups", type_="check")
    op.create_check_constraint("ck_follow_ups_kind", "follow_ups", NEW)


def downgrade() -> None:
    op.execute("DELETE FROM follow_ups WHERE kind = 'promise_write_back'")
    op.drop_constraint("ck_follow_ups_kind", "follow_ups", type_="check")
    op.create_check_constraint("ck_follow_ups_kind", "follow_ups", OLD)

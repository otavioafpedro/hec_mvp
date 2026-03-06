"""019_consumer_dashboard_snapshots - monthly PF dashboard series

Revision ID: 019_consumer_dashboard_snapshots
Revises: 018_consumer_reward_ledger
Create Date: 2026-03-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "019_consumer_dashboard_snapshots"
down_revision: Union[str, None] = "018_consumer_reward_ledger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "consumer_dashboard_snapshots",
        sa.Column("snapshot_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column("reference_month", sa.String(length=7), nullable=False),
        sa.Column("consumed_kwh", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("retired_mhec", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retirement_pct", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("co2_avoided_tons", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "reference_month", name="uq_consumer_snapshot_user_month"),
    )
    op.create_index(
        "ix_consumer_dashboard_snapshots_user_id",
        "consumer_dashboard_snapshots",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_consumer_dashboard_snapshots_user_id", table_name="consumer_dashboard_snapshots")
    op.drop_table("consumer_dashboard_snapshots")

"""018_consumer_reward_ledger - points and reward events

Revision ID: 018_consumer_reward_ledger
Revises: 017_consumer_dnft
Create Date: 2026-03-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "018_consumer_reward_ledger"
down_revision: Union[str, None] = "017_consumer_dnft"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "consumer_reward_ledger",
        sa.Column("ledger_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(length=30), nullable=False, server_default="manual"),
        sa.Column("source_ref", sa.String(length=100), nullable=True),
        sa.Column("points_delta", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mhec_delta", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("balance_after", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_consumer_reward_ledger_user_id", "consumer_reward_ledger", ["user_id"])
    op.create_index("ix_consumer_reward_ledger_created_at", "consumer_reward_ledger", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_consumer_reward_ledger_created_at", table_name="consumer_reward_ledger")
    op.drop_index("ix_consumer_reward_ledger_user_id", table_name="consumer_reward_ledger")
    op.drop_table("consumer_reward_ledger")

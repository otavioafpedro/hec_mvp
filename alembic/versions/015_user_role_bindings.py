"""015_user_role_bindings - multi-role user mapping

Revision ID: 015_user_role_bindings
Revises: 014_consumer_profiles
Create Date: 2026-03-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "015_user_role_bindings"
down_revision: Union[str, None] = "014_consumer_profiles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_role_bindings",
        sa.Column("binding_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column("role_code", sa.String(length=30), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "role_code", name="uq_user_role_binding"),
    )
    op.create_index("ix_user_role_bindings_user_id", "user_role_bindings", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_role_bindings_user_id", table_name="user_role_bindings")
    op.drop_table("user_role_bindings")

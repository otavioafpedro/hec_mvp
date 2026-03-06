"""014_consumer_profiles - PF/PJ consumer profile and counters

Revision ID: 014_consumer_profiles
Revises: 013_generator_onboarding
Create Date: 2026-03-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "014_consumer_profiles"
down_revision: Union[str, None] = "013_generator_onboarding"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "consumer_profiles",
        sa.Column("profile_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("person_type", sa.String(length=2), nullable=False, server_default="PF"),
        sa.Column("document_id", sa.String(length=32), nullable=True, unique=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("avatar_seed", sa.String(length=20), nullable=False, server_default="SOA"),
        sa.Column("plan_name", sa.String(length=60), nullable=False, server_default="Verde"),
        sa.Column("premmia_id", sa.String(length=50), nullable=True, unique=True),
        sa.Column("premmia_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_streak_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_retired_mhec", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_co2_avoided_tons", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("total_trees_equivalent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_referrals", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("joined_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_consumer_profiles_user_id", "consumer_profiles", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_consumer_profiles_user_id", table_name="consumer_profiles")
    op.drop_table("consumer_profiles")

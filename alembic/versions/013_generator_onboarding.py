"""013_generator_onboarding - Perfil de gerador e onboarding de inversor

Revision ID: 013_generator_onboarding
Revises: 012_precommitment_hash_len
Create Date: 2026-03-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "013_generator_onboarding"
down_revision: Union[str, None] = "012_precommitment_hash_len"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("plants", sa.Column("owner_user_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_plants_owner_user_id_users",
        "plants",
        "users",
        ["owner_user_id"],
        ["user_id"],
    )
    op.create_index("ix_plants_owner_user_id", "plants", ["owner_user_id"])

    op.create_table(
        "generator_profiles",
        sa.Column("profile_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("person_type", sa.String(length=2), nullable=False),
        sa.Column("document_id", sa.String(length=32), nullable=False, unique=True),
        sa.Column("legal_name", sa.String(length=255), nullable=True),
        sa.Column("trade_name", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column(
            "attribute_assignment_accepted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("assignment_accepted_at", sa.DateTime(), nullable=True),
        sa.Column(
            "onboarding_status",
            sa.String(length=30),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_generator_profiles_user_id", "generator_profiles", ["user_id"])

    op.create_table(
        "generator_inverter_connections",
        sa.Column("connection_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "profile_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generator_profiles.profile_id"),
            nullable=False,
        ),
        sa.Column(
            "plant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("plants.plant_id"),
            nullable=True,
        ),
        sa.Column("provider_name", sa.String(length=100), nullable=False),
        sa.Column("integration_mode", sa.String(length=30), nullable=False),
        sa.Column("external_account_ref", sa.String(length=255), nullable=True),
        sa.Column("inverter_serial", sa.String(length=100), nullable=True),
        sa.Column(
            "consent_accepted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("consented_at", sa.DateTime(), nullable=True),
        sa.Column(
            "connection_status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("last_sync_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_generator_inverter_connections_profile_id",
        "generator_inverter_connections",
        ["profile_id"],
    )
    op.create_index(
        "ix_generator_inverter_connections_plant_id",
        "generator_inverter_connections",
        ["plant_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_generator_inverter_connections_plant_id", table_name="generator_inverter_connections")
    op.drop_index("ix_generator_inverter_connections_profile_id", table_name="generator_inverter_connections")
    op.drop_table("generator_inverter_connections")

    op.drop_index("ix_generator_profiles_user_id", table_name="generator_profiles")
    op.drop_table("generator_profiles")

    op.drop_index("ix_plants_owner_user_id", table_name="plants")
    op.drop_constraint("fk_plants_owner_user_id_users", "plants", type_="foreignkey")
    op.drop_column("plants", "owner_user_id")

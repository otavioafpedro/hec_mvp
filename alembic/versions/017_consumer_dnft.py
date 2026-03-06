"""017_consumer_dnft - dNFT tiers, state and events

Revision ID: 017_consumer_dnft
Revises: 016_consumer_achievements
Create Date: 2026-03-05
"""
from datetime import datetime
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "017_consumer_dnft"
down_revision: Union[str, None] = "016_consumer_achievements"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dnft_definitions",
        sa.Column("dnft_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tier_level", sa.Integer(), nullable=False, unique=True),
        sa.Column("tier_name", sa.String(length=120), nullable=False),
        sa.Column("min_mhec_required", sa.Integer(), nullable=False),
        sa.Column("icon", sa.String(length=16), nullable=False, server_default="*"),
        sa.Column("benefits_json", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "user_dnft_states",
        sa.Column("state_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("current_tier_level", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("current_xp_mhec", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_tier_level", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("next_tier_target_mhec", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_pct", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_user_dnft_states_user_id", "user_dnft_states", ["user_id"])

    op.create_table(
        "user_dnft_events",
        sa.Column("event_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column("from_tier_level", sa.Integer(), nullable=True),
        sa.Column("to_tier_level", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False, server_default="upgrade"),
        sa.Column("event_payload", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_user_dnft_events_user_id", "user_dnft_events", ["user_id"])
    op.create_index("ix_user_dnft_events_created_at", "user_dnft_events", ["created_at"])

    dnft = sa.table(
        "dnft_definitions",
        sa.column("dnft_id", UUID(as_uuid=True)),
        sa.column("tier_level", sa.Integer()),
        sa.column("tier_name", sa.String(length=120)),
        sa.column("min_mhec_required", sa.Integer()),
        sa.column("icon", sa.String(length=16)),
        sa.column("benefits_json", JSONB),
        sa.column("created_at", sa.DateTime()),
    )

    now = datetime.utcnow()
    op.bulk_insert(
        dnft,
        [
            {
                "dnft_id": uuid.uuid4(),
                "tier_level": 1,
                "tier_name": "Semente",
                "min_mhec_required": 0,
                "icon": "seed",
                "benefits_json": ["Bem-vindo ao ecossistema SOA/SOS"],
                "created_at": now,
            },
            {
                "dnft_id": uuid.uuid4(),
                "tier_level": 3,
                "tier_name": "Broto",
                "min_mhec_required": 50,
                "icon": "sprout",
                "benefits_json": ["Selo de progressao inicial"],
                "created_at": now,
            },
            {
                "dnft_id": uuid.uuid4(),
                "tier_level": 5,
                "tier_name": "Arbusto",
                "min_mhec_required": 150,
                "icon": "flower",
                "benefits_json": ["Desconto inicial em compensacoes"],
                "created_at": now,
            },
            {
                "dnft_id": uuid.uuid4(),
                "tier_level": 7,
                "tier_name": "Bosque",
                "min_mhec_required": 300,
                "icon": "tree",
                "benefits_json": [
                    "12% desconto compensacao",
                    "Badge verificado",
                    "Relatorio mensal",
                ],
                "created_at": now,
            },
            {
                "dnft_id": uuid.uuid4(),
                "tier_level": 10,
                "tier_name": "Floresta",
                "min_mhec_required": 500,
                "icon": "forest",
                "benefits_json": [
                    "Relatorio ESG detalhado",
                    "Beneficios premium",
                ],
                "created_at": now,
            },
            {
                "dnft_id": uuid.uuid4(),
                "tier_level": 15,
                "tier_name": "Bioma",
                "min_mhec_required": 1500,
                "icon": "planet",
                "benefits_json": [
                    "Acesso prioritario a pools regionais",
                    "Reconhecimento comunidade",
                ],
                "created_at": now,
            },
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_user_dnft_events_created_at", table_name="user_dnft_events")
    op.drop_index("ix_user_dnft_events_user_id", table_name="user_dnft_events")
    op.drop_table("user_dnft_events")

    op.drop_index("ix_user_dnft_states_user_id", table_name="user_dnft_states")
    op.drop_table("user_dnft_states")

    op.drop_table("dnft_definitions")

"""016_consumer_achievements - catalog and user progress

Revision ID: 016_consumer_achievements
Revises: 015_user_role_bindings
Create Date: 2026-03-05
"""
from datetime import datetime
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "016_consumer_achievements"
down_revision: Union[str, None] = "015_user_role_bindings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "achievement_catalog",
        sa.Column("achievement_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(length=60), nullable=False, unique=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("icon", sa.String(length=16), nullable=False, server_default="*"),
        sa.Column(
            "metric_key",
            sa.String(length=50),
            nullable=False,
            server_default="total_retired_mhec",
        ),
        sa.Column("target_value", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("points_reward", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_achievement_catalog_code", "achievement_catalog", ["code"])

    op.create_table(
        "user_achievements",
        sa.Column("user_achievement_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column(
            "achievement_id",
            UUID(as_uuid=True),
            sa.ForeignKey("achievement_catalog.achievement_id"),
            nullable=False,
        ),
        sa.Column("progress_value", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_unlocked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("unlocked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "achievement_id", name="uq_user_achievement"),
    )
    op.create_index("ix_user_achievements_user_id", "user_achievements", ["user_id"])
    op.create_index("ix_user_achievements_achievement_id", "user_achievements", ["achievement_id"])

    catalog = sa.table(
        "achievement_catalog",
        sa.column("achievement_id", UUID(as_uuid=True)),
        sa.column("code", sa.String(length=60)),
        sa.column("name", sa.String(length=120)),
        sa.column("description", sa.Text()),
        sa.column("icon", sa.String(length=16)),
        sa.column("metric_key", sa.String(length=50)),
        sa.column("target_value", sa.Integer()),
        sa.column("points_reward", sa.Integer()),
        sa.column("is_active", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
        sa.column("created_at", sa.DateTime()),
    )

    now = datetime.utcnow()
    op.bulk_insert(
        catalog,
        [
            {
                "achievement_id": uuid.uuid4(),
                "code": "FIRST_RETIREMENT",
                "name": "Primeira Aposentadoria",
                "description": "Aposentou seu primeiro mHEC",
                "icon": "seed",
                "metric_key": "total_retired_mhec",
                "target_value": 1,
                "points_reward": 100,
                "is_active": True,
                "sort_order": 10,
                "created_at": now,
            },
            {
                "achievement_id": uuid.uuid4(),
                "code": "STREAK_7",
                "name": "Streak 7 dias",
                "description": "Compensou consumo por 7 dias seguidos",
                "icon": "flame",
                "metric_key": "current_streak_days",
                "target_value": 7,
                "points_reward": 120,
                "is_active": True,
                "sort_order": 20,
                "created_at": now,
            },
            {
                "achievement_id": uuid.uuid4(),
                "code": "STREAK_14",
                "name": "Streak 14 dias",
                "description": "Compensou consumo por 14 dias seguidos",
                "icon": "flame",
                "metric_key": "current_streak_days",
                "target_value": 14,
                "points_reward": 200,
                "is_active": True,
                "sort_order": 30,
                "created_at": now,
            },
            {
                "achievement_id": uuid.uuid4(),
                "code": "RETIRE_100",
                "name": "100 mHECs",
                "description": "Aposentou 100 mHECs no total",
                "icon": "tree",
                "metric_key": "total_retired_mhec",
                "target_value": 100,
                "points_reward": 180,
                "is_active": True,
                "sort_order": 40,
                "created_at": now,
            },
            {
                "achievement_id": uuid.uuid4(),
                "code": "RETIRE_300",
                "name": "300 mHECs",
                "description": "Atingiu nivel Bosque",
                "icon": "forest",
                "metric_key": "total_retired_mhec",
                "target_value": 300,
                "points_reward": 260,
                "is_active": True,
                "sort_order": 50,
                "created_at": now,
            },
            {
                "achievement_id": uuid.uuid4(),
                "code": "RETIRE_500",
                "name": "500 mHECs",
                "description": "Evoluir para Floresta",
                "icon": "forest",
                "metric_key": "total_retired_mhec",
                "target_value": 500,
                "points_reward": 320,
                "is_active": True,
                "sort_order": 60,
                "created_at": now,
            },
            {
                "achievement_id": uuid.uuid4(),
                "code": "RETIRE_1000",
                "name": "1000 mHECs",
                "description": "Equivalente a 1 MWh limpo",
                "icon": "bolt",
                "metric_key": "total_retired_mhec",
                "target_value": 1000,
                "points_reward": 500,
                "is_active": True,
                "sort_order": 70,
                "created_at": now,
            },
            {
                "achievement_id": uuid.uuid4(),
                "code": "CARBON_ZERO_MONTH",
                "name": "Carbono Zero Mes",
                "description": "Registrou 100% de compensacao no mes",
                "icon": "planet",
                "metric_key": "carbon_zero_months",
                "target_value": 1,
                "points_reward": 300,
                "is_active": True,
                "sort_order": 80,
                "created_at": now,
            },
            {
                "achievement_id": uuid.uuid4(),
                "code": "REFER_5_FRIENDS",
                "name": "Indicou 5 amigos",
                "description": "Convidou 5 amigos para o ecossistema",
                "icon": "team",
                "metric_key": "total_referrals",
                "target_value": 5,
                "points_reward": 220,
                "is_active": True,
                "sort_order": 90,
                "created_at": now,
            },
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_user_achievements_achievement_id", table_name="user_achievements")
    op.drop_index("ix_user_achievements_user_id", table_name="user_achievements")
    op.drop_table("user_achievements")

    op.drop_index("ix_achievement_catalog_code", table_name="achievement_catalog")
    op.drop_table("achievement_catalog")

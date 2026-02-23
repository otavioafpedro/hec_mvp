"""002_add_ntp_pass — Camada 2 NTP Blindada: flag de verificação ±5ms

Revision ID: 002_ntp_pass
Revises: 001_initial
Create Date: 2026-02-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002_ntp_pass"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "telemetry",
        sa.Column("ntp_pass", sa.Boolean, nullable=True,
                  comment="True se |drift| <= 5ms (Camada 2 NTP Blindada)"),
    )


def downgrade() -> None:
    op.drop_column("telemetry", "ntp_pass")

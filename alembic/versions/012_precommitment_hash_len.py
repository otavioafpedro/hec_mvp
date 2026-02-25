"""012_precommitment_hash_len - Expand telemetry.pre_commitment_hash length

Revision ID: 012_precommitment_hash_len
Revises: 011_burn
Create Date: 2026-02-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "012_precommitment_hash_len"
down_revision: Union[str, None] = "011_burn"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "telemetry",
        "pre_commitment_hash",
        existing_type=sa.String(length=128),
        type_=sa.String(length=256),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "telemetry",
        "pre_commitment_hash",
        existing_type=sa.String(length=256),
        type_=sa.String(length=128),
        existing_nullable=True,
    )

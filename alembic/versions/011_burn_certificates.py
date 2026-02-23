"""011_burn_certificates — Create burn_certificates table

Revision ID: 011_burn
Revises: 010_marketplace
Create Date: 2026-02-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "011_burn"
down_revision: Union[str, None] = "010_marketplace"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "burn_certificates",
        sa.Column("burn_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.user_id"), nullable=False, index=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("energy_kwh", sa.Numeric(16, 4), nullable=False),
        sa.Column("certificate_json", JSONB, nullable=True),
        sa.Column("hash_sha256", sa.String(64), unique=True, nullable=False),
        sa.Column("ipfs_json_cid", sa.String(100), nullable=True),
        sa.Column("ipfs_pdf_cid", sa.String(100), nullable=True),
        sa.Column("ipfs_provider", sa.String(20), nullable=True),
        sa.Column("registry_tx_hash", sa.String(66), nullable=True),
        sa.Column("registry_block", sa.Integer(), nullable=True),
        sa.Column("contract_address", sa.String(42), nullable=True),
        sa.Column("chain", sa.String(20), nullable=True),
        sa.Column("burned_hec_ids", JSONB, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="burned"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("burned_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("burn_certificates")

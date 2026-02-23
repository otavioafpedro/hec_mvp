"""008_onchain_registry — Add on-chain registry columns to hec_certificates

Revision ID: 008_onchain
Revises: 007_ipfs
Create Date: 2026-02-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "008_onchain"
down_revision: Union[str, None] = "007_ipfs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("hec_certificates",
        sa.Column("registry_tx_hash", sa.String(66), nullable=True,
                  comment="Transaction hash do registro on-chain (0x...)"))
    op.add_column("hec_certificates",
        sa.Column("registry_block", sa.Integer(), nullable=True,
                  comment="Block number do registro on-chain"))
    op.add_column("hec_certificates",
        sa.Column("registered_at", sa.DateTime(), nullable=True,
                  comment="Timestamp do registro on-chain"))

    # Update status comment to include 'registered'
    op.alter_column("hec_certificates", "status",
        comment="pending | registered | minted | listed | sold | retired")

    # Index for tx_hash lookup
    op.create_index("idx_hec_registry_tx_hash", "hec_certificates",
                    ["registry_tx_hash"], unique=True,
                    postgresql_where=sa.text("registry_tx_hash IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("idx_hec_registry_tx_hash", table_name="hec_certificates")
    op.drop_column("hec_certificates", "registered_at")
    op.drop_column("hec_certificates", "registry_block")
    op.drop_column("hec_certificates", "registry_tx_hash")

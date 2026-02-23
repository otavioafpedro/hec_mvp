"""007_ipfs_columns — Add IPFS JSON/PDF CID columns to hec_certificates

Revision ID: 007_ipfs
Revises: 006_hec_json
Create Date: 2026-02-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "007_ipfs"
down_revision: Union[str, None] = "006_hec_json"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("hec_certificates",
        sa.Column("ipfs_json_cid", sa.String(100), nullable=True,
                  comment="CID do JSON canônico no IPFS"))
    op.add_column("hec_certificates",
        sa.Column("ipfs_pdf_cid", sa.String(100), nullable=True,
                  comment="CID do PDF no IPFS"))
    op.add_column("hec_certificates",
        sa.Column("ipfs_provider", sa.String(20), nullable=True,
                  comment="Provider IPFS usado: mock | pinata | local"))


def downgrade() -> None:
    op.drop_column("hec_certificates", "ipfs_provider")
    op.drop_column("hec_certificates", "ipfs_pdf_cid")
    op.drop_column("hec_certificates", "ipfs_json_cid")

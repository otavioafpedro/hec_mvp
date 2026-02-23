"""006_hec_certificate_json — Add certificate_json to hec_certificates

Revision ID: 006_hec_json
Revises: 005_consensus
Create Date: 2026-02-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "006_hec_json"
down_revision: Union[str, None] = "005_consensus"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("hec_certificates",
        sa.Column("certificate_json", JSONB, nullable=True,
                  comment="JSON canônico completo do certificado HEC"))

    # Update default status from 'minted' to 'pending'
    op.alter_column("hec_certificates", "status",
        server_default="pending",
        comment="pending | minted | listed | sold | retired")


def downgrade() -> None:
    op.drop_column("hec_certificates", "certificate_json")
    op.alter_column("hec_certificates", "status",
        server_default="minted",
        comment="minted | listed | sold | retired")

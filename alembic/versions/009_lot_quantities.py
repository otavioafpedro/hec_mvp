"""009_lot_quantities — Add total_quantity, available_quantity, price_per_kwh to hec_lots

Revision ID: 009_lots
Revises: 008_onchain
Create Date: 2026-02-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "009_lots"
down_revision: Union[str, None] = "008_onchain"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("hec_lots",
        sa.Column("total_quantity", sa.Integer(), nullable=False, server_default="0",
                  comment="Quantidade total de HECs no lote"))
    op.add_column("hec_lots",
        sa.Column("available_quantity", sa.Integer(), nullable=False, server_default="0",
                  comment="Quantidade disponível (não vendidos/retirados)"))
    op.add_column("hec_lots",
        sa.Column("price_per_kwh", sa.Numeric(10, 4), nullable=True,
                  comment="Preço por kWh em BRL (se listado)"))


def downgrade() -> None:
    op.drop_column("hec_lots", "price_per_kwh")
    op.drop_column("hec_lots", "available_quantity")
    op.drop_column("hec_lots", "total_quantity")

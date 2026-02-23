"""010_marketplace — Create users, wallets, transactions tables

Revision ID: 010_marketplace
Revises: 009_lots
Create Date: 2026-02-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "010_marketplace"
down_revision: Union[str, None] = "009_lots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # USERS
    op.create_table(
        "users",
        sa.Column("user_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="buyer"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    # WALLETS
    op.create_table(
        "wallets",
        sa.Column("wallet_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.user_id"), unique=True, nullable=False),
        sa.Column("balance_brl", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("hec_balance", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("energy_balance_kwh", sa.Numeric(16, 4), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    # TRANSACTIONS
    op.create_table(
        "transactions",
        sa.Column("tx_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("buyer_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.user_id"), nullable=False, index=True),
        sa.Column("lot_id", UUID(as_uuid=True),
                  sa.ForeignKey("hec_lots.lot_id"), nullable=False, index=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("energy_kwh", sa.Numeric(16, 4), nullable=False),
        sa.Column("unit_price_brl", sa.Numeric(10, 4), nullable=False),
        sa.Column("total_price_brl", sa.Numeric(16, 2), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="completed"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("transactions")
    op.drop_table("wallets")
    op.drop_table("users")

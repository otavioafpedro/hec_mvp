"""020_inventory_custody_retirement

Revision ID: 020_inventory_custody_retirement
Revises: 019_consumer_dashboard_snapshots
Create Date: 2026-03-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "020_inventory_custody_retirement"
down_revision: Union[str, None] = "019_consumer_dashboard_snapshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("wallets", sa.Column("wallet_address", sa.String(length=42), nullable=True))

    op.add_column("transactions", sa.Column("source_hec_ids", JSONB, nullable=True))

    op.add_column("hec_lots", sa.Column("lot_manifest_json", JSONB, nullable=True))
    op.add_column("hec_lots", sa.Column("lot_manifest_cid", sa.String(length=100), nullable=True))
    op.add_column("hec_lots", sa.Column("batch_hash", sa.String(length=64), nullable=True))
    op.add_column("hec_lots", sa.Column("onchain_batch_token_id", sa.Integer(), nullable=True))
    op.add_column("hec_lots", sa.Column("onchain_total_units", sa.Integer(), nullable=True))
    op.add_column("hec_lots", sa.Column("onchain_issued_tx_hash", sa.String(length=66), nullable=True))
    op.add_column("hec_lots", sa.Column("onchain_issued_block", sa.Integer(), nullable=True))
    op.add_column(
        "hec_lots",
        sa.Column(
            "custody_mode",
            sa.String(length=30),
            nullable=False,
            server_default="platform_custody",
        ),
    )
    op.add_column(
        "hec_lots",
        sa.Column(
            "transferability_policy",
            sa.String(length=30),
            nullable=False,
            server_default="non_transferable",
        ),
    )
    op.add_column(
        "hec_lots",
        sa.Column(
            "inventory_status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
    )

    op.add_column(
        "burn_certificates",
        sa.Column("retired_mhec", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("burn_certificates", sa.Column("claimant_wallet", sa.String(length=42), nullable=True))
    op.add_column("burn_certificates", sa.Column("beneficiary_ref", sa.String(length=255), nullable=True))
    op.add_column("burn_certificates", sa.Column("beneficiary_ref_hash", sa.String(length=64), nullable=True))
    op.add_column("burn_certificates", sa.Column("external_operation_id", sa.String(length=100), nullable=True))
    op.add_column("burn_certificates", sa.Column("retirement_event_ids", JSONB, nullable=True))
    op.add_column("burn_certificates", sa.Column("receipt_token_ids", JSONB, nullable=True))

    op.create_table(
        "inventory_positions",
        sa.Column("position_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("wallet_id", UUID(as_uuid=True), sa.ForeignKey("wallets.wallet_id"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("lot_id", UUID(as_uuid=True), sa.ForeignKey("hec_lots.lot_id"), nullable=False),
        sa.Column("transaction_id", UUID(as_uuid=True), sa.ForeignKey("transactions.tx_id"), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("available_quantity", sa.Integer(), nullable=False),
        sa.Column("retired_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("energy_kwh_total", sa.Numeric(16, 4), nullable=False),
        sa.Column("energy_kwh_available", sa.Numeric(16, 4), nullable=False),
        sa.Column("source_hec_ids", JSONB, nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_inventory_positions_wallet_id", "inventory_positions", ["wallet_id"])
    op.create_index("ix_inventory_positions_user_id", "inventory_positions", ["user_id"])
    op.create_index("ix_inventory_positions_lot_id", "inventory_positions", ["lot_id"])
    op.create_index("ix_inventory_positions_transaction_id", "inventory_positions", ["transaction_id"])

    op.create_table(
        "retirement_events",
        sa.Column("retirement_event_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("burn_id", UUID(as_uuid=True), sa.ForeignKey("burn_certificates.burn_id"), nullable=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("lot_id", UUID(as_uuid=True), sa.ForeignKey("hec_lots.lot_id"), nullable=False),
        sa.Column("batch_token_id", sa.Integer(), nullable=True),
        sa.Column("amount_hec", sa.Integer(), nullable=False),
        sa.Column("amount_mhec", sa.Integer(), nullable=False),
        sa.Column("claimant_wallet", sa.String(length=42), nullable=True),
        sa.Column("beneficiary_ref", sa.String(length=255), nullable=True),
        sa.Column("beneficiary_ref_hash", sa.String(length=64), nullable=True),
        sa.Column("external_operation_id", sa.String(length=100), nullable=True),
        sa.Column("protocol_operator", sa.String(length=50), nullable=False, server_default="platform_custody"),
        sa.Column("onchain_retirement_id", sa.Integer(), nullable=True),
        sa.Column("receipt_token_id", sa.Integer(), nullable=True),
        sa.Column("receipt_contract_address", sa.String(length=42), nullable=True),
        sa.Column("retirement_tx_hash", sa.String(length=66), nullable=True),
        sa.Column("retirement_block", sa.Integer(), nullable=True),
        sa.Column("source_hec_ids", JSONB, nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="retired"),
        sa.Column("retired_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_retirement_events_burn_id", "retirement_events", ["burn_id"])
    op.create_index("ix_retirement_events_user_id", "retirement_events", ["user_id"])
    op.create_index("ix_retirement_events_lot_id", "retirement_events", ["lot_id"])


def downgrade() -> None:
    op.drop_index("ix_retirement_events_lot_id", table_name="retirement_events")
    op.drop_index("ix_retirement_events_user_id", table_name="retirement_events")
    op.drop_index("ix_retirement_events_burn_id", table_name="retirement_events")
    op.drop_table("retirement_events")

    op.drop_index("ix_inventory_positions_transaction_id", table_name="inventory_positions")
    op.drop_index("ix_inventory_positions_lot_id", table_name="inventory_positions")
    op.drop_index("ix_inventory_positions_user_id", table_name="inventory_positions")
    op.drop_index("ix_inventory_positions_wallet_id", table_name="inventory_positions")
    op.drop_table("inventory_positions")

    op.drop_column("burn_certificates", "receipt_token_ids")
    op.drop_column("burn_certificates", "retirement_event_ids")
    op.drop_column("burn_certificates", "external_operation_id")
    op.drop_column("burn_certificates", "beneficiary_ref_hash")
    op.drop_column("burn_certificates", "beneficiary_ref")
    op.drop_column("burn_certificates", "claimant_wallet")
    op.drop_column("burn_certificates", "retired_mhec")

    op.drop_column("hec_lots", "inventory_status")
    op.drop_column("hec_lots", "transferability_policy")
    op.drop_column("hec_lots", "custody_mode")
    op.drop_column("hec_lots", "onchain_issued_block")
    op.drop_column("hec_lots", "onchain_issued_tx_hash")
    op.drop_column("hec_lots", "onchain_total_units")
    op.drop_column("hec_lots", "onchain_batch_token_id")
    op.drop_column("hec_lots", "batch_hash")
    op.drop_column("hec_lots", "lot_manifest_cid")
    op.drop_column("hec_lots", "lot_manifest_json")

    op.drop_column("transactions", "source_hec_ids")
    op.drop_column("wallets", "wallet_address")

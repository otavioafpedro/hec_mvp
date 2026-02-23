"""001_initial_schema — plants, telemetry, validations, hec_certificates, hec_lots

Revision ID: 001_initial
Revises:
Create Date: 2026-02-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable TimescaleDB
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;")

    # --- PLANTS ---
    op.create_table(
        "plants",
        sa.Column("plant_id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("absolar_id", sa.String(100), unique=True, nullable=True),
        sa.Column("owner_name", sa.String(255), nullable=True),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lng", sa.Float, nullable=False),
        sa.Column("capacity_kw", sa.Numeric(12, 3), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("inverter_brand", sa.String(100), nullable=True),
        sa.Column("inverter_model", sa.String(100), nullable=True),
        sa.Column("commissioning_date", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )

    # --- HEC_LOTS ---
    op.create_table(
        "hec_lots",
        sa.Column("lot_id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("total_energy_kwh", sa.Numeric(16, 4), nullable=False, server_default="0"),
        sa.Column("certificate_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )

    # --- TELEMETRY (hypertable TimescaleDB) ---
    op.create_table(
        "telemetry",
        sa.Column("id", UUID(as_uuid=True), nullable=False,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("time", sa.DateTime, nullable=False),
        sa.Column("plant_id", UUID(as_uuid=True), sa.ForeignKey("plants.plant_id"), nullable=False),
        sa.Column("power_kw", sa.Numeric(12, 4), nullable=False),
        sa.Column("energy_kwh", sa.Numeric(14, 4), nullable=False),
        sa.Column("voltage_v", sa.Numeric(8, 2), nullable=True),
        sa.Column("temperature_c", sa.Numeric(6, 2), nullable=True),
        sa.Column("irradiance_wm2", sa.Numeric(8, 2), nullable=True),
        sa.Column("source", sa.String(50), nullable=False, server_default="mqtt"),
        sa.Column("pre_commitment_hash", sa.String(128), nullable=True),
        sa.Column("ntp_delta_ms", sa.Float, nullable=True),
        sa.Column("raw_payload", JSONB, nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=True),
        sa.Column("nonce", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_telemetry_time", "telemetry", ["time"])
    op.create_index("ix_telemetry_plant_id", "telemetry", ["plant_id"])
    op.create_index("ix_telemetry_nonce", "telemetry", ["nonce"], unique=False)

    # Hypertable — chave primária composta (time, id) exigida pelo TimescaleDB
    op.execute("ALTER TABLE telemetry ADD PRIMARY KEY (time, id);")
    op.execute("SELECT create_hypertable('telemetry', 'time', migrate_data => true);")

    # --- VALIDATIONS ---
    op.create_table(
        "validations",
        sa.Column("validation_id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("plant_id", UUID(as_uuid=True), sa.ForeignKey("plants.plant_id"), nullable=False),
        sa.Column("period_start", sa.DateTime, nullable=False),
        sa.Column("period_end", sa.DateTime, nullable=False),
        sa.Column("energy_kwh", sa.Numeric(14, 4), nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("anomaly_flags", JSONB, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("sentinel_version", sa.String(20), nullable=True),
        sa.Column("validation_details", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_validations_plant_id", "validations", ["plant_id"])

    # --- HEC_CERTIFICATES ---
    op.create_table(
        "hec_certificates",
        sa.Column("hec_id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("validation_id", UUID(as_uuid=True),
                  sa.ForeignKey("validations.validation_id"), unique=True, nullable=False),
        sa.Column("lot_id", UUID(as_uuid=True),
                  sa.ForeignKey("hec_lots.lot_id"), nullable=True),
        sa.Column("hash_sha256", sa.String(64), unique=True, nullable=False),
        sa.Column("energy_kwh", sa.Numeric(14, 4), nullable=False),
        sa.Column("token_id", sa.String(100), nullable=True),
        sa.Column("contract_address", sa.String(42), nullable=True),
        sa.Column("chain", sa.String(20), nullable=True, server_default="polygon"),
        sa.Column("ipfs_cid", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="minted"),
        sa.Column("minted_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )

    # --- NONCE REPLAY PROTECTION TABLE ---
    op.create_table(
        "used_nonces",
        sa.Column("nonce", sa.String(64), primary_key=True),
        sa.Column("plant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("used_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_used_nonces_used_at", "used_nonces", ["used_at"])


def downgrade() -> None:
    op.drop_table("used_nonces")
    op.drop_table("hec_certificates")
    op.drop_table("validations")
    op.drop_table("telemetry")
    op.drop_table("hec_lots")
    op.drop_table("plants")
    op.execute("DROP EXTENSION IF EXISTS timescaledb CASCADE;")

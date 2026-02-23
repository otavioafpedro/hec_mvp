"""003_physics_validation — Camada 3 Física Teórica (pvlib)

Adds: telemetry_id, ntp_pass, ntp_drift_ms, theoretical_max_kwh,
      theoretical_max_kw, ghi_clear_sky_wm2, solar_elevation_deg,
      physics_pass, physics_method

Revision ID: 003_physics
Revises: 002_ntp_pass
Create Date: 2026-02-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "003_physics"
down_revision: Union[str, None] = "002_ntp_pass"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("validations",
        sa.Column("telemetry_id", UUID(as_uuid=True), nullable=True,
                  comment="ID da telemetria que originou esta validação"))
    op.add_column("validations",
        sa.Column("ntp_pass", sa.Boolean, nullable=True,
                  comment="True se |drift NTP| <= 5ms"))
    op.add_column("validations",
        sa.Column("ntp_drift_ms", sa.Float, nullable=True,
                  comment="Drift NTP medido (ms)"))
    op.add_column("validations",
        sa.Column("theoretical_max_kwh", sa.Numeric(14, 4), nullable=True,
                  comment="Geração máxima teórica no intervalo (kWh)"))
    op.add_column("validations",
        sa.Column("theoretical_max_kw", sa.Numeric(12, 4), nullable=True,
                  comment="Potência máxima teórica instantânea (kW)"))
    op.add_column("validations",
        sa.Column("ghi_clear_sky_wm2", sa.Numeric(8, 2), nullable=True,
                  comment="Irradiância clear-sky estimada (W/m²)"))
    op.add_column("validations",
        sa.Column("solar_elevation_deg", sa.Numeric(6, 2), nullable=True,
                  comment="Elevação solar (graus)"))
    op.add_column("validations",
        sa.Column("physics_pass", sa.Boolean, nullable=True,
                  comment="True se energy_kwh <= theoretical_max_kwh"))
    op.add_column("validations",
        sa.Column("physics_method", sa.String(20), nullable=True,
                  comment="pvlib | analytical"))


def downgrade() -> None:
    op.drop_column("validations", "physics_method")
    op.drop_column("validations", "physics_pass")
    op.drop_column("validations", "solar_elevation_deg")
    op.drop_column("validations", "ghi_clear_sky_wm2")
    op.drop_column("validations", "theoretical_max_kw")
    op.drop_column("validations", "theoretical_max_kwh")
    op.drop_column("validations", "ntp_drift_ms")
    op.drop_column("validations", "ntp_pass")
    op.drop_column("validations", "telemetry_id")

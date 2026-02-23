"""004_satellite_validation — Camada 4 Validação Satélite (INPE GOES-16 / CAMS)

Adds: satellite_ghi_wm2, satellite_source, satellite_max_kwh,
      satellite_pass, cloud_cover_pct, satellite_flags

Revision ID: 004_satellite
Revises: 003_physics
Create Date: 2026-02-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "004_satellite"
down_revision: Union[str, None] = "003_physics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("validations",
        sa.Column("satellite_ghi_wm2", sa.Numeric(8, 2), nullable=True,
                  comment="Irradiância GHI medida por satélite (W/m²)"))
    op.add_column("validations",
        sa.Column("satellite_source", sa.String(30), nullable=True,
                  comment="mock | inpe_goes16 | cams_copernicus"))
    op.add_column("validations",
        sa.Column("satellite_max_kwh", sa.Numeric(14, 4), nullable=True,
                  comment="Geração máx baseada em irradiância satélite (kWh)"))
    op.add_column("validations",
        sa.Column("satellite_pass", sa.Boolean, nullable=True,
                  comment="True se energy_kwh <= satellite_max_kwh"))
    op.add_column("validations",
        sa.Column("cloud_cover_pct", sa.Numeric(5, 1), nullable=True,
                  comment="Cobertura de nuvens estimada (%)"))
    op.add_column("validations",
        sa.Column("satellite_flags", JSONB, nullable=True,
                  comment="Flags: low_irradiance, high_generation_low_sun"))


def downgrade() -> None:
    op.drop_column("validations", "satellite_flags")
    op.drop_column("validations", "cloud_cover_pct")
    op.drop_column("validations", "satellite_pass")
    op.drop_column("validations", "satellite_max_kwh")
    op.drop_column("validations", "satellite_source")
    op.drop_column("validations", "satellite_ghi_wm2")

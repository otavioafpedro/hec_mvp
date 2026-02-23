"""005_consensus_geospatial — Camada 5 Consenso Granular Geoespacial

Enables PostGIS extension and adds consensus validation columns.

Revision ID: 005_consensus
Revises: 004_satellite
Create Date: 2026-02-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "005_consensus"
down_revision: Union[str, None] = "004_satellite"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostGIS extension (idempotent — won't fail if already exists)
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    # Consensus columns
    op.add_column("validations",
        sa.Column("consensus_pass", sa.Boolean, nullable=True,
                  comment="True=ok, False=divergente, None=inconclusivo"))
    op.add_column("validations",
        sa.Column("consensus_deviation_pct", sa.Numeric(6, 2), nullable=True,
                  comment="Desvio percentual da mediana vizinhas"))
    op.add_column("validations",
        sa.Column("consensus_median_ratio", sa.Numeric(10, 6), nullable=True,
                  comment="Mediana kWh/kWp das vizinhas"))
    op.add_column("validations",
        sa.Column("consensus_plant_ratio", sa.Numeric(10, 6), nullable=True,
                  comment="Ratio kWh/kWp da planta alvo"))
    op.add_column("validations",
        sa.Column("consensus_neighbors", sa.Integer, nullable=True,
                  comment="Qtde vizinhas usadas no consenso"))
    op.add_column("validations",
        sa.Column("consensus_radius_km", sa.Numeric(6, 2), nullable=True,
                  comment="Raio de busca usado (km)"))
    op.add_column("validations",
        sa.Column("consensus_details", JSONB, nullable=True,
                  comment="Detalhes: vizinhas, distâncias, ratios"))

    # Spatial index for neighbor lookups (PostGIS)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_plants_geom
        ON plants USING GIST (
            ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography
        )
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_plants_geom")
    op.drop_column("validations", "consensus_details")
    op.drop_column("validations", "consensus_radius_km")
    op.drop_column("validations", "consensus_neighbors")
    op.drop_column("validations", "consensus_plant_ratio")
    op.drop_column("validations", "consensus_median_ratio")
    op.drop_column("validations", "consensus_deviation_pct")
    op.drop_column("validations", "consensus_pass")

"""
Testes automatizados — Camada 5: Consenso Granular Geoespacial

Cenários cobertos:

  UNIT TESTS — Haversine:
    ✅ SP → SP (~0km)
    ✅ SP → vizinha 2km
    ✅ SP → vizinha 100km (fora do raio)
    ✅ Equador → polo (grande distância)

  UNIT TESTS — Consenso lógica:
    ✅ Planta alinhada com vizinhas → consensus_pass=True
    ❌ Planta divergente (>30% da mediana) → consensus_pass=False
    ✅ Sem vizinhas suficientes → consensus_pass=None (inconclusivo)
    ✅ Mediana zero + planta zero → desvio 0% → pass
    ❌ Mediana zero + planta positiva → desvio 100% → fail
    ✅ Boundary: desvio exatamente 30% → pass (<=)
    ❌ Boundary: desvio 30.01% → fail

  INTEGRATION TESTS (POST /telemetry com cluster de vizinhas):
    ✅ Cluster de 3 plantas similares → accepted, consensus_pass=True
    ❌ Planta isolada divergente → review, consensus_pass=False
    ✅ Planta sem vizinhas → accepted, consensus_pass=None
    ✅ Validation record persiste consensus data
"""
import uuid
import math
from datetime import datetime, timezone, timedelta

import pytest

from app.consensus import (
    haversine_km,
    find_neighbors,
    get_neighbor_readings,
    validate_consensus,
    _median,
    ConsensusResult,
    NeighborReading,
    RADIUS_KM,
    DEVIATION_THRESHOLD_PCT,
    TIME_WINDOW_MINUTES,
    MIN_NEIGHBORS,
)
from app.models.models import Plant, Telemetry
from app.security import canonical_payload, sign_payload
from app.api.telemetry import set_server_now_fn, reset_server_now_fn
from app.satellite import MockSatelliteProvider, set_satellite_provider, reset_satellite_provider


SEED_PLANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
NEIGHBOR_1_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
NEIGHBOR_2_ID = uuid.UUID("00000000-0000-0000-0000-000000000003")
NEIGHBOR_3_ID = uuid.UUID("00000000-0000-0000-0000-000000000004")
FAR_PLANT_ID  = uuid.UUID("00000000-0000-0000-0000-000000000005")

# São Paulo area coordinates
SP_LAT, SP_LNG = -23.55, -46.63

NOON_UTC = datetime(2026, 2, 23, 14, 30, 0, 0, tzinfo=timezone.utc)

# Fixed satellite mock (high GHI so satellite doesn't interfere)
_SAT_PROVIDER = MockSatelliteProvider(fixed_ghi_wm2=800.0, fixed_cloud_cover_pct=10.0)


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def _fixed_clock(dt: datetime):
    return lambda: dt


def _make_payload(private_pem, public_pem, plant_id, timestamp_str, energy_kwh, nonce=None):
    nonce = nonce or uuid.uuid4().hex[:32]
    canon = canonical_payload(str(plant_id), timestamp_str, 5.5, energy_kwh, nonce)
    signature = sign_payload(private_pem, canon)
    return {
        "plant_id": str(plant_id),
        "timestamp": timestamp_str,
        "power_kw": 5.5,
        "energy_kwh": energy_kwh,
        "signature": signature,
        "public_key": public_pem,
        "nonce": nonce,
    }


def _seed_neighbor_plants(db):
    """Seed 3 neighbor plants within 5km + 1 far plant."""
    neighbors = [
        Plant(
            plant_id=NEIGHBOR_1_ID,
            name="Vizinha 1 (2km)",
            absolar_id="N-001",
            lat=-23.532,       # ~2km north of seed
            lng=-46.63,
            capacity_kw=80.0,
            status="active",
        ),
        Plant(
            plant_id=NEIGHBOR_2_ID,
            name="Vizinha 2 (3km)",
            absolar_id="N-002",
            lat=-23.55,
            lng=-46.60,        # ~3km east of seed
            capacity_kw=60.0,
            status="active",
        ),
        Plant(
            plant_id=NEIGHBOR_3_ID,
            name="Vizinha 3 (4km)",
            absolar_id="N-003",
            lat=-23.52,
            lng=-46.64,        # ~4km northwest of seed
            capacity_kw=100.0,
            status="active",
        ),
        Plant(
            plant_id=FAR_PLANT_ID,
            name="Planta Distante (50km)",
            absolar_id="F-001",
            lat=-23.10,        # ~50km north — outside radius
            lng=-46.63,
            capacity_kw=50.0,
            status="active",
        ),
    ]
    for p in neighbors:
        db.add(p)
    db.commit()
    return neighbors


def _seed_telemetry(db, plant_id, energy_kwh, time=None):
    """Insert telemetry record for a plant."""
    time = time or NOON_UTC.replace(tzinfo=None)  # SQLite compat
    t = Telemetry(
        id=uuid.uuid4(),
        time=time,
        plant_id=plant_id,
        power_kw=5.0,
        energy_kwh=energy_kwh,
        source="api",
    )
    db.add(t)
    db.commit()
    return t


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — Haversine distance
# ═══════════════════════════════════════════════════════════════════

class TestHaversine:
    """Testes da fórmula de Haversine para distância GPS."""

    def test_same_point_zero(self):
        assert haversine_km(SP_LAT, SP_LNG, SP_LAT, SP_LNG) == 0.0

    def test_sp_to_neighbor_2km(self):
        """~2km north."""
        dist = haversine_km(SP_LAT, SP_LNG, -23.532, -46.63)
        assert 1.5 < dist < 2.5, f"Expected ~2km, got {dist:.2f}km"

    def test_sp_to_neighbor_3km(self):
        """~3km east."""
        dist = haversine_km(SP_LAT, SP_LNG, -23.55, -46.60)
        assert 2.5 < dist < 3.5, f"Expected ~3km, got {dist:.2f}km"

    def test_sp_to_far_50km(self):
        """~50km north — fora do raio 5km."""
        dist = haversine_km(SP_LAT, SP_LNG, -23.10, -46.63)
        assert dist > 40, f"Expected > 40km, got {dist:.2f}km"

    def test_equator_to_pole(self):
        """Grande distância: equador ao polo norte."""
        dist = haversine_km(0, 0, 90, 0)
        assert 9900 < dist < 10100, f"Expected ~10000km, got {dist:.0f}km"

    def test_symmetric(self):
        """Distância simétrica: A→B == B→A."""
        d1 = haversine_km(SP_LAT, SP_LNG, -23.532, -46.63)
        d2 = haversine_km(-23.532, -46.63, SP_LAT, SP_LNG)
        assert abs(d1 - d2) < 0.001


class TestMedian:
    """Testes do cálculo de mediana."""

    def test_odd_count(self):
        assert _median([1, 3, 5]) == 3

    def test_even_count(self):
        assert _median([1, 3, 5, 7]) == 4.0

    def test_single_value(self):
        assert _median([42]) == 42

    def test_empty_list(self):
        assert _median([]) == 0.0

    def test_unsorted_input(self):
        assert _median([5, 1, 3]) == 3


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — find_neighbors (with DB)
# ═══════════════════════════════════════════════════════════════════

class TestFindNeighbors:
    """Busca de vizinhas com Haversine fallback."""

    def test_finds_neighbors_within_radius(self, db_session):
        _seed_neighbor_plants(db_session)
        target = db_session.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()

        neighbors = find_neighbors(db_session, target, radius_km=5.0)

        # Should find 3 neighbors (within 5km), not the far one
        assert len(neighbors) == 3
        ids = {str(p.plant_id) for p, _ in neighbors}
        assert str(NEIGHBOR_1_ID) in ids
        assert str(NEIGHBOR_2_ID) in ids
        assert str(NEIGHBOR_3_ID) in ids
        assert str(FAR_PLANT_ID) not in ids

    def test_excludes_far_plant(self, db_session):
        _seed_neighbor_plants(db_session)
        target = db_session.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()

        neighbors = find_neighbors(db_session, target, radius_km=5.0)
        far_ids = {str(p.plant_id) for p, _ in neighbors}
        assert str(FAR_PLANT_ID) not in far_ids

    def test_sorted_by_distance(self, db_session):
        _seed_neighbor_plants(db_session)
        target = db_session.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()

        neighbors = find_neighbors(db_session, target, radius_km=5.0)
        distances = [d for _, d in neighbors]
        assert distances == sorted(distances)

    def test_excludes_inactive_plants(self, db_session):
        """Planta inativa não deve aparecer como vizinha."""
        _seed_neighbor_plants(db_session)
        # Mark neighbor 1 as inactive
        n1 = db_session.query(Plant).filter(Plant.plant_id == NEIGHBOR_1_ID).first()
        n1.status = "inactive"
        db_session.commit()

        target = db_session.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()
        neighbors = find_neighbors(db_session, target, radius_km=5.0)
        ids = {str(p.plant_id) for p, _ in neighbors}
        assert str(NEIGHBOR_1_ID) not in ids
        assert len(neighbors) == 2

    def test_no_neighbors_empty_list(self, db_session):
        """Sem vizinhas no raio → lista vazia."""
        target = db_session.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()
        neighbors = find_neighbors(db_session, target, radius_km=5.0)
        assert len(neighbors) == 0

    def test_custom_radius(self, db_session):
        """Raio 3km → só encontra vizinhas mais próximas."""
        _seed_neighbor_plants(db_session)
        target = db_session.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()

        neighbors = find_neighbors(db_session, target, radius_km=3.0)
        # Only neighbor 1 (~2km) and neighbor 2 (~3km) should be within 3km
        assert len(neighbors) >= 1
        for _, dist in neighbors:
            assert dist <= 3.0


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — validate_consensus
# ═══════════════════════════════════════════════════════════════════

class TestConsensusLogic:
    """Testes da lógica de consenso granular."""

    def test_aligned_plants_pass(self, db_session):
        """Vizinhas geram ~0.16 kWh/kWp, alvo gera 0.164 → desvio baixo → pass."""
        _seed_neighbor_plants(db_session)
        target = db_session.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()

        # Seed telemetry for neighbors (similar ratios)
        # N1: 80kWp, 12.8 kWh → ratio=0.16
        # N2: 60kWp, 9.6 kWh → ratio=0.16
        # N3: 100kWp, 16.0 kWh → ratio=0.16
        _seed_telemetry(db_session, NEIGHBOR_1_ID, 12.8)
        _seed_telemetry(db_session, NEIGHBOR_2_ID, 9.6)
        _seed_telemetry(db_session, NEIGHBOR_3_ID, 16.0)

        # Target: 75kWp, 12.3 kWh → ratio=0.164
        result = validate_consensus(
            db_session, target, energy_kwh=12.3,
            reference_time=NOON_UTC,
        )

        assert result.consensus_pass is True
        assert result.neighbors_used == 3
        assert result.deviation_pct < 30.0
        assert result.median_ratio == pytest.approx(0.16, abs=0.001)

    def test_divergent_plant_fails(self, db_session):
        """Vizinhas geram ~0.16, alvo gera 0.50 → desvio >30% → fail."""
        _seed_neighbor_plants(db_session)
        target = db_session.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()

        _seed_telemetry(db_session, NEIGHBOR_1_ID, 12.8)  # 0.16
        _seed_telemetry(db_session, NEIGHBOR_2_ID, 9.6)   # 0.16
        _seed_telemetry(db_session, NEIGHBOR_3_ID, 16.0)  # 0.16

        # Target: 75kWp, 37.5 kWh → ratio=0.50 (3x the median!)
        result = validate_consensus(
            db_session, target, energy_kwh=37.5,
            reference_time=NOON_UTC,
        )

        assert result.consensus_pass is False
        assert result.deviation_pct > 30.0
        assert result.plant_ratio == pytest.approx(0.5, abs=0.001)

    def test_extremely_divergent_plant(self, db_session):
        """Vizinhas geram ~0.16, alvo reporta 60 kWh (ratio=0.80) → flagrante."""
        _seed_neighbor_plants(db_session)
        target = db_session.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()

        _seed_telemetry(db_session, NEIGHBOR_1_ID, 12.8)
        _seed_telemetry(db_session, NEIGHBOR_2_ID, 9.6)
        _seed_telemetry(db_session, NEIGHBOR_3_ID, 16.0)

        result = validate_consensus(
            db_session, target, energy_kwh=60.0,
            reference_time=NOON_UTC,
        )

        assert result.consensus_pass is False
        assert result.deviation_pct > 100.0  # Way over

    def test_insufficient_neighbors_inconclusive(self, db_session):
        """Apenas 1 vizinha com telemetria → inconclusivo (min=2)."""
        _seed_neighbor_plants(db_session)
        target = db_session.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()

        # Only seed telemetry for 1 neighbor
        _seed_telemetry(db_session, NEIGHBOR_1_ID, 12.8)

        result = validate_consensus(
            db_session, target, energy_kwh=12.3,
            reference_time=NOON_UTC,
        )

        assert result.consensus_pass is None
        assert result.neighbors_used == 1
        assert result.deviation_pct is None

    def test_no_neighbors_inconclusive(self, db_session):
        """Nenhuma vizinha → inconclusivo."""
        target = db_session.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()

        result = validate_consensus(
            db_session, target, energy_kwh=12.3,
            reference_time=NOON_UTC,
        )

        assert result.consensus_pass is None
        assert result.neighbors_used == 0
        assert result.neighbor_count == 0

    def test_boundary_exactly_30pct_passes(self, db_session):
        """Desvio exatamente 30% → pass (<=)."""
        _seed_neighbor_plants(db_session)
        target = db_session.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()

        # Median ratio = 0.16
        _seed_telemetry(db_session, NEIGHBOR_1_ID, 12.8)
        _seed_telemetry(db_session, NEIGHBOR_2_ID, 9.6)
        _seed_telemetry(db_session, NEIGHBOR_3_ID, 16.0)

        # Target: ratio = 0.16 × 1.30 = 0.208 → desvio = 30%
        target_energy = 0.208 * 75.0  # 15.6 kWh
        result = validate_consensus(
            db_session, target, energy_kwh=target_energy,
            reference_time=NOON_UTC,
        )

        assert result.deviation_pct == pytest.approx(30.0, abs=0.5)
        assert result.consensus_pass is True

    def test_boundary_just_above_30pct_fails(self, db_session):
        """Desvio 35% → fail."""
        _seed_neighbor_plants(db_session)
        target = db_session.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()

        _seed_telemetry(db_session, NEIGHBOR_1_ID, 12.8)
        _seed_telemetry(db_session, NEIGHBOR_2_ID, 9.6)
        _seed_telemetry(db_session, NEIGHBOR_3_ID, 16.0)

        # Target: ratio = 0.16 × 1.35 = 0.216 → desvio = 35%
        target_energy = 0.216 * 75.0  # 16.2 kWh
        result = validate_consensus(
            db_session, target, energy_kwh=target_energy,
            reference_time=NOON_UTC,
        )

        assert result.deviation_pct > 30.0
        assert result.consensus_pass is False

    def test_zero_median_zero_plant_passes(self, db_session):
        """Vizinhas e alvo geram 0 → desvio 0% → pass."""
        _seed_neighbor_plants(db_session)
        target = db_session.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()

        _seed_telemetry(db_session, NEIGHBOR_1_ID, 0.0)
        _seed_telemetry(db_session, NEIGHBOR_2_ID, 0.0)
        _seed_telemetry(db_session, NEIGHBOR_3_ID, 0.0)

        result = validate_consensus(
            db_session, target, energy_kwh=0.0,
            reference_time=NOON_UTC,
        )

        assert result.consensus_pass is True
        assert result.deviation_pct == 0.0

    def test_zero_median_positive_plant_fails(self, db_session):
        """Vizinhas geram 0, alvo gera positivo → desvio 100% → fail."""
        _seed_neighbor_plants(db_session)
        target = db_session.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()

        _seed_telemetry(db_session, NEIGHBOR_1_ID, 0.0)
        _seed_telemetry(db_session, NEIGHBOR_2_ID, 0.0)
        _seed_telemetry(db_session, NEIGHBOR_3_ID, 0.0)

        result = validate_consensus(
            db_session, target, energy_kwh=10.0,
            reference_time=NOON_UTC,
        )

        assert result.consensus_pass is False

    def test_old_telemetry_excluded(self, db_session):
        """Telemetria fora da janela ±30min → não conta como vizinha."""
        _seed_neighbor_plants(db_session)
        target = db_session.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()

        # Telemetry 2 hours ago — outside window
        old_time = (NOON_UTC - timedelta(hours=2)).replace(tzinfo=None)
        _seed_telemetry(db_session, NEIGHBOR_1_ID, 12.8, time=old_time)
        _seed_telemetry(db_session, NEIGHBOR_2_ID, 9.6, time=old_time)
        _seed_telemetry(db_session, NEIGHBOR_3_ID, 16.0, time=old_time)

        result = validate_consensus(
            db_session, target, energy_kwh=12.3,
            reference_time=NOON_UTC,
        )

        assert result.consensus_pass is None  # No recent telemetry
        assert result.neighbors_used == 0

    def test_result_has_neighbor_details(self, db_session):
        """Resultado contém detalhes das vizinhas."""
        _seed_neighbor_plants(db_session)
        target = db_session.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()

        _seed_telemetry(db_session, NEIGHBOR_1_ID, 12.8)
        _seed_telemetry(db_session, NEIGHBOR_2_ID, 9.6)

        result = validate_consensus(
            db_session, target, energy_kwh=12.3,
            reference_time=NOON_UTC,
        )

        assert len(result.neighbors) == 2
        for n in result.neighbors:
            assert isinstance(n, NeighborReading)
            assert n.distance_km > 0
            assert n.capacity_kw > 0

    def test_plant_below_median_divergent(self, db_session):
        """Planta gera MUITO MENOS que vizinhas → também flaggeia."""
        _seed_neighbor_plants(db_session)
        target = db_session.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()

        # Neighbors generate a lot: ratio ~0.4
        _seed_telemetry(db_session, NEIGHBOR_1_ID, 32.0)  # 0.4
        _seed_telemetry(db_session, NEIGHBOR_2_ID, 24.0)  # 0.4
        _seed_telemetry(db_session, NEIGHBOR_3_ID, 40.0)  # 0.4

        # Target: 75kWp, 5 kWh → ratio=0.0667 (83% below median)
        result = validate_consensus(
            db_session, target, energy_kwh=5.0,
            reference_time=NOON_UTC,
        )

        assert result.consensus_pass is False
        assert result.deviation_pct > 30.0


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — POST /telemetry com cluster de vizinhas
# ═══════════════════════════════════════════════════════════════════

class TestConsensusIntegration:
    """Testes de integração do consenso via endpoint."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        set_server_now_fn(_fixed_clock(NOON_UTC))
        set_satellite_provider(_SAT_PROVIDER)
        yield
        reset_server_now_fn()
        reset_satellite_provider()

    def test_aligned_cluster_accepted(self, client, db_session, ecdsa_keys):
        """Cluster de 3 vizinhas similares + alvo alinhado → accepted."""
        _seed_neighbor_plants(db_session)
        _seed_telemetry(db_session, NEIGHBOR_1_ID, 12.8)  # 0.16
        _seed_telemetry(db_session, NEIGHBOR_2_ID, 9.6)   # 0.16
        _seed_telemetry(db_session, NEIGHBOR_3_ID, 16.0)  # 0.16

        private_pem, public_pem = ecdsa_keys
        ts_str = NOON_UTC.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = _make_payload(
            private_pem, public_pem, SEED_PLANT_ID, ts_str, energy_kwh=12.3
        )

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        data = r.json()

        assert data["status"] == "accepted"
        assert data["consensus_pass"] is True
        assert data["consensus_neighbors"] == 3
        assert data["consensus_deviation_pct"] is not None
        assert data["consensus_deviation_pct"] < 30.0

    def test_divergent_plant_review(self, client, db_session, ecdsa_keys):
        """Planta divergente (3x a mediana) → review."""
        _seed_neighbor_plants(db_session)
        _seed_telemetry(db_session, NEIGHBOR_1_ID, 12.8)
        _seed_telemetry(db_session, NEIGHBOR_2_ID, 9.6)
        _seed_telemetry(db_session, NEIGHBOR_3_ID, 16.0)

        private_pem, public_pem = ecdsa_keys
        ts_str = NOON_UTC.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        # 37.5 kWh → ratio=0.50 vs median=0.16 → desvio 212%!
        payload = _make_payload(
            private_pem, public_pem, SEED_PLANT_ID, ts_str, energy_kwh=37.5
        )

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        data = r.json()

        assert data["consensus_pass"] is False
        assert data["consensus_deviation_pct"] > 30.0
        # Score: sig=20, ntp=20, phys=30, sat=15, cons=0 = 85 → REVIEW
        assert data["confidence_score"] == 85.0
        assert data["status"] == "review"
        assert "C5" in data["message"] or "consenso" in data["message"].lower()

    def test_no_neighbors_inconclusive_still_accepted(self, client, db_session, ecdsa_keys):
        """Sem vizinhas → consenso inconclusivo, mas pode ser accepted."""
        # No neighbor plants seeded — only seed plant
        private_pem, public_pem = ecdsa_keys
        ts_str = NOON_UTC.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = _make_payload(
            private_pem, public_pem, SEED_PLANT_ID, ts_str, energy_kwh=12.3
        )

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        data = r.json()

        assert data["consensus_pass"] is None
        assert data["consensus_neighbors"] == 0
        # Não penaliza por inconclusivo
        assert data["confidence_score"] == 100.0

    def test_validation_record_has_consensus(self, client, db_session, ecdsa_keys):
        """Validation persiste todos os dados de consenso."""
        _seed_neighbor_plants(db_session)
        _seed_telemetry(db_session, NEIGHBOR_1_ID, 12.8)
        _seed_telemetry(db_session, NEIGHBOR_2_ID, 9.6)
        _seed_telemetry(db_session, NEIGHBOR_3_ID, 16.0)

        private_pem, public_pem = ecdsa_keys
        ts_str = NOON_UTC.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = _make_payload(
            private_pem, public_pem, SEED_PLANT_ID, ts_str, energy_kwh=37.5
        )

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        data = r.json()

        from app.models.models import Validation as Val
        val = db_session.query(Val).filter(
            Val.validation_id == data["validation_id"]
        ).first()

        assert val is not None
        assert val.consensus_pass is False
        assert float(val.consensus_deviation_pct) > 30.0
        assert float(val.consensus_median_ratio) == pytest.approx(0.16, abs=0.01)
        assert float(val.consensus_plant_ratio) == pytest.approx(0.50, abs=0.01)
        assert val.consensus_neighbors == 3
        assert float(val.consensus_radius_km) == RADIUS_KM
        # consensus_details has neighbor info
        assert val.consensus_details is not None
        assert "neighbors" in val.consensus_details
        assert len(val.consensus_details["neighbors"]) == 3

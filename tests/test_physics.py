"""
Testes automatizados — Camada 3: Validação Física Teórica (pvlib / Patente 33)

Cenários cobertos:

  UNIT TESTS (compute_theoretical_max — puro, sem banco):
    ✅ Meio-dia solar SP: GHI > 0, theoretical_max > 0
    ✅ Geração válida (12 kWh < max teórico ~69 kWh) → physics_pass=True
    ❌ Geração impossível (200 kWh > max para 75kWp) → physics_pass=False
    ❌ Noite (3:00 UTC em SP): elevação < 0, GHI=0, max=0, qualquer kWh → False
    ✅ Noite com 0 kWh → physics_pass=True (0 <= 0)
    ❌ Energia negativa ou absurda
    ✅ Diferentes localizações: Manaus (equador), Porto Alegre (sul)
    ✅ Margem de segurança: energia ligeiramente abaixo do max → passa

  INTEGRATION TESTS (POST /telemetry):
    ✅ Energia razoável meio-dia → 201, physics_pass=True, status=accepted
    ❌ Energia impossível meio-dia → 201, physics_pass=False, status=review
    ❌ Qualquer energia positiva à noite → 201, physics_pass=False, status=review
    ✅ Validation record criado com theoretical_max e physics_pass
"""
import uuid
import math
from datetime import datetime, timezone, timedelta

import pytest

from app.physics import (
    compute_theoretical_max,
    _solar_elevation_deg,
    _clear_sky_ghi_wm2,
    _solar_declination_deg,
    PhysicsResult,
    SAFETY_MARGIN,
    STC_IRRADIANCE_WM2,
)
from app.security import canonical_payload, sign_payload
from app.api.telemetry import set_server_now_fn, reset_server_now_fn
from app.satellite import MockSatelliteProvider, set_satellite_provider, reset_satellite_provider


SEED_PLANT_ID = "00000000-0000-0000-0000-000000000001"

# São Paulo: lat=-23.55, lng=-46.63, capacity=75kWp (da seed)
SP_LAT = -23.55
SP_LNG = -46.63
CAPACITY_KW = 75.0

# Fixed satellite for physics integration tests (high GHI, no interference)
_SAT_PROVIDER = MockSatelliteProvider(fixed_ghi_wm2=800.0, fixed_cloud_cover_pct=10.0)


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def _fixed_clock(dt: datetime):
    return lambda: dt


def _make_physics_payload(private_pem, public_pem, timestamp_str, energy_kwh, nonce=None):
    """Helper: payload assinado com timestamp e energy_kwh customizados."""
    nonce = nonce or uuid.uuid4().hex[:32]
    canon = canonical_payload(
        SEED_PLANT_ID, timestamp_str, 5.5, energy_kwh, nonce,
    )
    signature = sign_payload(private_pem, canon)
    return {
        "plant_id": SEED_PLANT_ID,
        "timestamp": timestamp_str,
        "power_kw": 5.5,
        "energy_kwh": energy_kwh,
        "signature": signature,
        "public_key": public_pem,
        "nonce": nonce,
    }


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — Posição Solar (equações analíticas)
# ═══════════════════════════════════════════════════════════════════

class TestSolarPosition:
    """Verificação das equações de posição solar."""

    def test_declination_summer_solstice(self):
        """Solstício de verão (dia ~172): declinação ≈ +23.45°."""
        decl = _solar_declination_deg(172)
        assert 20.0 < decl < 24.0, f"Expected ~23.45°, got {decl}°"

    def test_declination_winter_solstice(self):
        """Solstício de inverno (dia ~355): declinação ≈ -23.45°."""
        decl = _solar_declination_deg(355)
        assert -24.0 < decl < -20.0, f"Expected ~-23.45°, got {decl}°"

    def test_declination_equinox(self):
        """Equinócio (dia ~81): declinação ≈ 0°."""
        decl = _solar_declination_deg(81)
        assert abs(decl) < 2.0, f"Expected ~0°, got {decl}°"

    def test_sp_noon_utc_elevation_positive(self):
        """SP a meio-dia solar (~14:30 UTC em fev): elevação deve ser alta."""
        dt = datetime(2026, 2, 23, 14, 30, tzinfo=timezone.utc)
        elev = _solar_elevation_deg(SP_LAT, SP_LNG, dt)
        assert elev > 40, f"Expected > 40° near noon, got {elev:.1f}°"

    def test_sp_midnight_utc_elevation_negative(self):
        """SP a meia-noite UTC (21h local): sol abaixo do horizonte."""
        dt = datetime(2026, 2, 23, 3, 0, tzinfo=timezone.utc)
        elev = _solar_elevation_deg(SP_LAT, SP_LNG, dt)
        assert elev < 0, f"Expected negative (night), got {elev:.1f}°"

    def test_equator_noon_very_high(self):
        """Equador (lat=0) ao meio-dia: elevação muito alta."""
        dt = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)  # equinócio ~
        elev = _solar_elevation_deg(0.0, 0.0, dt)
        assert elev > 70, f"Expected > 70° at equator noon equinox, got {elev:.1f}°"


class TestClearSkyGHI:
    """Verificação do modelo clear-sky."""

    def test_positive_for_high_elevation(self):
        """Elevação solar 60° → GHI significativo."""
        ghi = _clear_sky_ghi_wm2(60.0)
        assert 600 < ghi < 1200, f"Expected 600-1200 W/m², got {ghi:.0f}"

    def test_zero_for_negative_elevation(self):
        """Sol abaixo do horizonte → GHI = 0."""
        assert _clear_sky_ghi_wm2(-5.0) == 0.0
        assert _clear_sky_ghi_wm2(-30.0) == 0.0

    def test_zero_at_horizon(self):
        """Sol exatamente no horizonte → GHI = 0."""
        assert _clear_sky_ghi_wm2(0.0) == 0.0

    def test_low_for_low_elevation(self):
        """Elevação 5° → GHI baixo mas positivo."""
        ghi = _clear_sky_ghi_wm2(5.0)
        assert 0 < ghi < 200, f"Expected < 200 W/m² at 5°, got {ghi:.0f}"

    def test_altitude_increases_ghi(self):
        """Altitude maior → GHI maior (ar mais fino)."""
        ghi_sea = _clear_sky_ghi_wm2(45.0, altitude_m=0)
        ghi_mountain = _clear_sky_ghi_wm2(45.0, altitude_m=2000)
        assert ghi_mountain > ghi_sea


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — compute_theoretical_max (validação física completa)
# ═══════════════════════════════════════════════════════════════════

class TestPhysicsValidation:
    """Testes do motor de validação física."""

    # ── Cenários de dia (meio-dia solar SP, fev) ──────────────────

    NOON_SP = datetime(2026, 2, 23, 14, 30, tzinfo=timezone.utc)

    def test_valid_generation_passes(self):
        """12.3 kWh para 75kWp ao meio-dia → physics_pass=True."""
        result = compute_theoretical_max(
            lat=SP_LAT, lng=SP_LNG, capacity_kw=CAPACITY_KW,
            timestamp=self.NOON_SP, reported_kwh=12.3,
            force_analytical=True,
        )
        assert result.physics_pass is True
        assert result.theoretical_max_kwh > 12.3
        assert result.solar_elevation_deg > 30
        assert result.ghi_clear_sky_wm2 > 0
        assert result.method == "analytical"

    def test_impossible_generation_fails(self):
        """200 kWh para 75kWp → impossível → physics_pass=False."""
        result = compute_theoretical_max(
            lat=SP_LAT, lng=SP_LNG, capacity_kw=CAPACITY_KW,
            timestamp=self.NOON_SP, reported_kwh=200.0,
            force_analytical=True,
        )
        assert result.physics_pass is False
        assert result.reported_kwh == 200.0
        assert result.theoretical_max_kwh < 200.0

    def test_extreme_generation_fails(self):
        """500 kWh para 75kWp → absurdamente impossível."""
        result = compute_theoretical_max(
            lat=SP_LAT, lng=SP_LNG, capacity_kw=CAPACITY_KW,
            timestamp=self.NOON_SP, reported_kwh=500.0,
            force_analytical=True,
        )
        assert result.physics_pass is False

    def test_zero_generation_always_passes(self):
        """0 kWh → sempre válido (inversor desligado/manutenção)."""
        result = compute_theoretical_max(
            lat=SP_LAT, lng=SP_LNG, capacity_kw=CAPACITY_KW,
            timestamp=self.NOON_SP, reported_kwh=0.0,
            force_analytical=True,
        )
        assert result.physics_pass is True

    def test_max_at_capacity(self):
        """Máximo teórico não pode exceder capacity_kw × safety_margin × 1h."""
        result = compute_theoretical_max(
            lat=SP_LAT, lng=SP_LNG, capacity_kw=CAPACITY_KW,
            timestamp=self.NOON_SP, reported_kwh=0.0,
            force_analytical=True,
        )
        hard_cap = CAPACITY_KW * SAFETY_MARGIN  # 75 × 1.15 = 86.25 kWh max/h
        assert result.theoretical_max_kwh <= hard_cap + 0.1

    # ── Cenários de noite ─────────────────────────────────────────

    NIGHT_SP = datetime(2026, 2, 23, 3, 0, tzinfo=timezone.utc)  # ~midnight SP

    def test_night_zero_passes(self):
        """0 kWh à noite → physics_pass=True (0 <= 0)."""
        result = compute_theoretical_max(
            lat=SP_LAT, lng=SP_LNG, capacity_kw=CAPACITY_KW,
            timestamp=self.NIGHT_SP, reported_kwh=0.0,
            force_analytical=True,
        )
        assert result.physics_pass is True
        assert result.theoretical_max_kwh == 0.0
        assert result.ghi_clear_sky_wm2 == 0.0
        assert result.solar_elevation_deg < 0

    def test_night_any_positive_fails(self):
        """Qualquer geração positiva à noite → impossível."""
        result = compute_theoretical_max(
            lat=SP_LAT, lng=SP_LNG, capacity_kw=CAPACITY_KW,
            timestamp=self.NIGHT_SP, reported_kwh=0.1,
            force_analytical=True,
        )
        assert result.physics_pass is False
        assert result.theoretical_max_kwh == 0.0

    def test_night_large_generation_fails(self):
        """50 kWh à noite → claramente impossível."""
        result = compute_theoretical_max(
            lat=SP_LAT, lng=SP_LNG, capacity_kw=CAPACITY_KW,
            timestamp=self.NIGHT_SP, reported_kwh=50.0,
            force_analytical=True,
        )
        assert result.physics_pass is False

    # ── Cenários com localizações diferentes ──────────────────────

    def test_manaus_equator_high_ghi(self):
        """Manaus (lat~-3, equador): GHI alto ao meio-dia."""
        noon_manaus = datetime(2026, 2, 23, 16, 0, tzinfo=timezone.utc)  # ~12h local
        result = compute_theoretical_max(
            lat=-3.12, lng=-60.02, capacity_kw=100.0,
            timestamp=noon_manaus, reported_kwh=10.0,
            force_analytical=True,
        )
        assert result.physics_pass is True
        assert result.solar_elevation_deg > 50
        assert result.ghi_clear_sky_wm2 > 500

    def test_porto_alegre_south(self):
        """Porto Alegre (lat~-30): sol mais baixo que equador."""
        noon_poa = datetime(2026, 2, 23, 15, 0, tzinfo=timezone.utc)
        result = compute_theoretical_max(
            lat=-30.03, lng=-51.23, capacity_kw=50.0,
            timestamp=noon_poa, reported_kwh=5.0,
            force_analytical=True,
        )
        assert result.physics_pass is True
        assert result.solar_elevation_deg > 20

    # ── Cenário boundary: energia no limite ───────────────────────

    def test_energy_at_exact_max_passes(self):
        """Energia exatamente = theoretical_max → physics_pass=True (<=)."""
        # Primeiro compute o max
        result = compute_theoretical_max(
            lat=SP_LAT, lng=SP_LNG, capacity_kw=CAPACITY_KW,
            timestamp=self.NOON_SP, reported_kwh=0.0,
            force_analytical=True,
        )
        max_kwh = result.theoretical_max_kwh

        # Agora reportar exatamente o max
        result2 = compute_theoretical_max(
            lat=SP_LAT, lng=SP_LNG, capacity_kw=CAPACITY_KW,
            timestamp=self.NOON_SP, reported_kwh=max_kwh,
            force_analytical=True,
        )
        assert result2.physics_pass is True

    def test_energy_just_above_max_fails(self):
        """Energia 0.01 kWh acima do theoretical_max → physics_pass=False."""
        result = compute_theoretical_max(
            lat=SP_LAT, lng=SP_LNG, capacity_kw=CAPACITY_KW,
            timestamp=self.NOON_SP, reported_kwh=0.0,
            force_analytical=True,
        )
        max_kwh = result.theoretical_max_kwh

        result2 = compute_theoretical_max(
            lat=SP_LAT, lng=SP_LNG, capacity_kw=CAPACITY_KW,
            timestamp=self.NOON_SP, reported_kwh=max_kwh + 0.01,
            force_analytical=True,
        )
        assert result2.physics_pass is False

    # ── PhysicsResult data completeness ───────────────────────────

    def test_result_has_all_fields(self):
        """Resultado contém todos os campos esperados."""
        result = compute_theoretical_max(
            lat=SP_LAT, lng=SP_LNG, capacity_kw=CAPACITY_KW,
            timestamp=self.NOON_SP, reported_kwh=10.0,
            force_analytical=True,
        )
        assert isinstance(result, PhysicsResult)
        assert isinstance(result.theoretical_max_kwh, float)
        assert isinstance(result.theoretical_max_kw, float)
        assert isinstance(result.ghi_clear_sky_wm2, float)
        assert isinstance(result.solar_elevation_deg, float)
        assert isinstance(result.physics_pass, bool)
        assert result.reported_kwh == 10.0
        assert result.capacity_kw == CAPACITY_KW
        assert result.interval_hours == 1.0
        assert result.method == "analytical"


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — POST /telemetry com validação física
# ═══════════════════════════════════════════════════════════════════

class TestPhysicsIntegrationAccepted:
    """Geração válida → 201, physics_pass=True, status=accepted."""

    NOON_UTC = datetime(2026, 2, 23, 14, 30, 0, 0, tzinfo=timezone.utc)

    @pytest.fixture(autouse=True)
    def _setup_clock(self):
        set_server_now_fn(_fixed_clock(self.NOON_UTC))
        set_satellite_provider(_SAT_PROVIDER)
        yield
        reset_server_now_fn()
        reset_satellite_provider()

    def test_reasonable_energy_accepted(self, client, ecdsa_keys):
        """12.3 kWh para 75kWp ao meio-dia → accepted + physics_pass=True."""
        private_pem, public_pem = ecdsa_keys
        ts_str = self.NOON_UTC.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = _make_physics_payload(private_pem, public_pem, ts_str, energy_kwh=12.3)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        data = r.json()

        assert data["status"] == "accepted"
        assert data["physics_pass"] is True
        assert data["theoretical_max_kwh"] > 12.3
        assert data["solar_elevation_deg"] > 30
        assert data["ghi_clear_sky_wm2"] > 0
        assert data["validation_id"] is not None

    def test_small_energy_accepted(self, client, ecdsa_keys):
        """1 kWh → trivially valid."""
        private_pem, public_pem = ecdsa_keys
        ts_str = self.NOON_UTC.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = _make_physics_payload(private_pem, public_pem, ts_str, energy_kwh=1.0)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        assert r.json()["physics_pass"] is True

    def test_zero_energy_accepted(self, client, ecdsa_keys):
        """0 kWh → valid (inversor desligado)."""
        private_pem, public_pem = ecdsa_keys
        ts_str = self.NOON_UTC.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = _make_physics_payload(private_pem, public_pem, ts_str, energy_kwh=0.0)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        assert r.json()["physics_pass"] is True


class TestPhysicsIntegrationRejected:
    """Geração impossível → 201, physics_pass=False, status=rejected (score 70)."""

    NOON_UTC = datetime(2026, 2, 23, 14, 30, 0, 0, tzinfo=timezone.utc)
    NIGHT_UTC = datetime(2026, 2, 23, 3, 0, 0, 0, tzinfo=timezone.utc)

    @pytest.fixture(autouse=True)
    def _setup_clock_noon(self):
        """Default: relógio no meio-dia + satellite fixo."""
        set_server_now_fn(_fixed_clock(self.NOON_UTC))
        set_satellite_provider(_SAT_PROVIDER)
        yield
        reset_server_now_fn()
        reset_satellite_provider()

    def test_impossible_energy_rejected(self, client, ecdsa_keys):
        """200 kWh para 75kWp → impossível → rejected (score 70)."""
        private_pem, public_pem = ecdsa_keys
        ts_str = self.NOON_UTC.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = _make_physics_payload(private_pem, public_pem, ts_str, energy_kwh=200.0)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        data = r.json()

        assert data["physics_pass"] is False
        assert data["status"] == "rejected"
        assert data["theoretical_max_kwh"] < 200.0
        assert data["confidence_score"] == 70.0

    def test_extreme_energy_review(self, client, ecdsa_keys):
        """500 kWh → absurdamente impossível."""
        private_pem, public_pem = ecdsa_keys
        ts_str = self.NOON_UTC.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = _make_physics_payload(private_pem, public_pem, ts_str, energy_kwh=500.0)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        assert r.json()["physics_pass"] is False

    def test_night_generation_review(self, client, ecdsa_keys):
        """5 kWh à noite → impossível (sol abaixo horizonte)."""
        private_pem, public_pem = ecdsa_keys
        set_server_now_fn(_fixed_clock(self.NIGHT_UTC))
        ts_str = self.NIGHT_UTC.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = _make_physics_payload(private_pem, public_pem, ts_str, energy_kwh=5.0)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        data = r.json()

        assert data["physics_pass"] is False
        assert data["theoretical_max_kwh"] == 0.0
        assert data["solar_elevation_deg"] < 0

    def test_night_tiny_generation_review(self, client, ecdsa_keys):
        """0.1 kWh à noite → impossível (qualquer positivo)."""
        private_pem, public_pem = ecdsa_keys
        set_server_now_fn(_fixed_clock(self.NIGHT_UTC))
        ts_str = self.NIGHT_UTC.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = _make_physics_payload(private_pem, public_pem, ts_str, energy_kwh=0.1)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        assert r.json()["physics_pass"] is False

    def test_validation_record_has_physics_data(self, client, ecdsa_keys, db_session):
        """Validation record persiste theoretical_max e physics_pass."""
        private_pem, public_pem = ecdsa_keys
        ts_str = self.NOON_UTC.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = _make_physics_payload(private_pem, public_pem, ts_str, energy_kwh=200.0)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        data = r.json()

        # Query validation from DB
        from app.models.models import Validation as Val
        val = db_session.query(Val).filter(
            Val.validation_id == data["validation_id"]
        ).first()

        assert val is not None
        assert val.physics_pass is False
        assert float(val.theoretical_max_kwh) < 200.0
        assert float(val.theoretical_max_kwh) > 0
        assert float(val.energy_kwh) == 200.0
        assert val.physics_method in ("pvlib", "analytical")
        assert val.status in ("review", "rejected")

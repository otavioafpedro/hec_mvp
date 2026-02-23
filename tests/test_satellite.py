"""
Testes automatizados — Camada 4: Validação Satélite (Cross-validation Orbital)

Cenários cobertos:

  UNIT TESTS (validate_satellite — puro, sem banco):
    ✅ GHI alto (800 W/m²) + geração baixa → satellite_pass=True
    ❌ GHI alto + geração excede max → satellite_pass=False
    ✅ GHI moderado (400 W/m²) + geração compatível → pass
    ❌ GHI baixo (100 W/m²) + geração alta → fail + low_irradiance + high_gen flag
    ✅ GHI baixo + geração zero → pass (inversor desligado)
    ❌ GHI zero (noite) + qualquer geração → fail
    ✅ GHI zero + zero geração → pass
    ✅ Boundary: energia exatamente no satellite_max → pass
    ❌ Boundary: energia 0.01 acima do satellite_max → fail

  MOCK PROVIDER TESTS:
    ✅ Fixed mode retorna valores exatos
    ✅ Auto mode retorna GHI > 0 durante o dia
    ✅ Auto mode retorna GHI ≈ 0 à noite

  INTEGRATION TESTS (POST /telemetry com mock provider injetado):
    ✅ GHI alto + geração baixa → accepted, satellite_pass=True
    ❌ GHI baixo + geração alta → review, satellite_pass=False
    ❌ GHI muito baixo + geração alta → review + flag high_generation_low_sun
    ✅ Validation record persiste satellite_ghi, satellite_pass, cloud_cover
    ✅ Confidence score: satellite fail reduz score
"""
import uuid
from datetime import datetime, timezone, timedelta

import pytest

from app.satellite import (
    validate_satellite,
    MockSatelliteProvider,
    INPESatelliteProvider,
    CAMSSatelliteProvider,
    SatelliteReading,
    SatelliteValidationResult,
    set_satellite_provider,
    get_satellite_provider,
    reset_satellite_provider,
    SATELLITE_SAFETY_MARGIN,
    LOW_IRRADIANCE_THRESHOLD_WM2,
    STC_IRRADIANCE_WM2,
)
from app.security import canonical_payload, sign_payload
from app.api.telemetry import set_server_now_fn, reset_server_now_fn


SEED_PLANT_ID = "00000000-0000-0000-0000-000000000001"
SP_LAT = -23.55
SP_LNG = -46.63
CAPACITY_KW = 75.0

NOON_UTC = datetime(2026, 2, 23, 14, 30, 0, 0, tzinfo=timezone.utc)
NIGHT_UTC = datetime(2026, 2, 23, 3, 0, 0, 0, tzinfo=timezone.utc)


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def _fixed_clock(dt: datetime):
    return lambda: dt


def _make_sat_payload(private_pem, public_pem, timestamp_str, energy_kwh, nonce=None):
    """Payload assinado com timestamp e energy_kwh customizados."""
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
# UNIT TESTS — MockSatelliteProvider
# ═══════════════════════════════════════════════════════════════════

class TestMockSatelliteProvider:
    """Verifica funcionamento do provider mock."""

    def test_fixed_mode_returns_exact_values(self):
        provider = MockSatelliteProvider(fixed_ghi_wm2=750.0, fixed_cloud_cover_pct=25.0)
        reading = provider.fetch_irradiance(SP_LAT, SP_LNG, NOON_UTC)
        assert reading.ghi_wm2 == 750.0
        assert reading.cloud_cover_pct == 25.0
        assert reading.source == "mock"
        assert reading.lat == SP_LAT
        assert reading.lng == SP_LNG

    def test_fixed_mode_zero_ghi(self):
        provider = MockSatelliteProvider(fixed_ghi_wm2=0.0, fixed_cloud_cover_pct=100.0)
        reading = provider.fetch_irradiance(SP_LAT, SP_LNG, NOON_UTC)
        assert reading.ghi_wm2 == 0.0
        assert reading.cloud_cover_pct == 100.0

    def test_auto_mode_daytime_positive_ghi(self):
        provider = MockSatelliteProvider(add_noise=False)
        reading = provider.fetch_irradiance(SP_LAT, SP_LNG, NOON_UTC)
        assert reading.ghi_wm2 > 0
        assert reading.source == "mock"
        assert reading.raw_data["mode"] == "auto"

    def test_auto_mode_nighttime_zero_ghi(self):
        provider = MockSatelliteProvider(add_noise=False)
        reading = provider.fetch_irradiance(SP_LAT, SP_LNG, NIGHT_UTC)
        assert reading.ghi_wm2 == 0.0

    def test_provider_name(self):
        assert MockSatelliteProvider().name == "mock"
        assert INPESatelliteProvider().name == "inpe_goes16"
        assert CAMSSatelliteProvider().name == "cams_copernicus"

    def test_confidence_value(self):
        reading = MockSatelliteProvider(fixed_ghi_wm2=500.0).fetch_irradiance(
            SP_LAT, SP_LNG, NOON_UTC
        )
        assert 0 < reading.confidence <= 1.0


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — validate_satellite (lógica de cross-validation)
# ═══════════════════════════════════════════════════════════════════

class TestSatelliteValidationLogic:
    """Testes puros da lógica de validação por satélite."""

    # ── GHI alto (céu limpo) ─────────────────────────────────────

    def test_high_ghi_low_generation_passes(self):
        """GHI=800 + 12 kWh para 75kWp → satellite_pass=True."""
        # sat_max = 75 × (800/1000) × 1.20 × 1 = 72 kWh
        provider = MockSatelliteProvider(fixed_ghi_wm2=800.0, fixed_cloud_cover_pct=10.0)
        result = validate_satellite(
            SP_LAT, SP_LNG, CAPACITY_KW, NOON_UTC,
            reported_kwh=12.0, provider=provider,
        )
        assert result.satellite_pass is True
        assert result.satellite_ghi_wm2 == 800.0
        assert result.satellite_max_kwh > 12.0
        assert result.low_irradiance is False
        assert result.high_generation_low_sun is False

    def test_high_ghi_high_generation_fails(self):
        """GHI=800 + 100 kWh → excede sat_max (72 kWh) → fail."""
        provider = MockSatelliteProvider(fixed_ghi_wm2=800.0, fixed_cloud_cover_pct=10.0)
        result = validate_satellite(
            SP_LAT, SP_LNG, CAPACITY_KW, NOON_UTC,
            reported_kwh=100.0, provider=provider,
        )
        assert result.satellite_pass is False
        assert result.satellite_max_kwh < 100.0

    # ── GHI moderado (parcialmente nublado) ──────────────────────

    def test_moderate_ghi_compatible_generation(self):
        """GHI=400 + 20 kWh → sat_max=36 → pass."""
        provider = MockSatelliteProvider(fixed_ghi_wm2=400.0, fixed_cloud_cover_pct=50.0)
        result = validate_satellite(
            SP_LAT, SP_LNG, CAPACITY_KW, NOON_UTC,
            reported_kwh=20.0, provider=provider,
        )
        assert result.satellite_pass is True
        # sat_max = 75 × 0.4 × 1.2 = 36
        assert result.satellite_max_kwh == pytest.approx(36.0, abs=0.1)

    def test_moderate_ghi_excessive_generation(self):
        """GHI=400 + 50 kWh → excede sat_max (36 kWh) → fail."""
        provider = MockSatelliteProvider(fixed_ghi_wm2=400.0, fixed_cloud_cover_pct=50.0)
        result = validate_satellite(
            SP_LAT, SP_LNG, CAPACITY_KW, NOON_UTC,
            reported_kwh=50.0, provider=provider,
        )
        assert result.satellite_pass is False

    # ── GHI baixo (nublado pesado / chuva) ───────────────────────

    def test_low_ghi_flags_low_irradiance(self):
        """GHI=100 < threshold (150) → low_irradiance=True."""
        provider = MockSatelliteProvider(fixed_ghi_wm2=100.0, fixed_cloud_cover_pct=80.0)
        result = validate_satellite(
            SP_LAT, SP_LNG, CAPACITY_KW, NOON_UTC,
            reported_kwh=0.5, provider=provider,
        )
        assert result.low_irradiance is True
        assert result.cloud_cover_pct == 80.0

    def test_low_ghi_high_generation_severe_flag(self):
        """GHI=100 + 30 kWh → flag SEVERA: high_generation_low_sun."""
        # sat_max = 75 × (100/1000) × 1.2 = 9 kWh
        # high_gen_threshold = 75 × 0.10 = 7.5 kWh
        # 30 > 7.5 AND low_irradiance → high_generation_low_sun
        provider = MockSatelliteProvider(fixed_ghi_wm2=100.0, fixed_cloud_cover_pct=85.0)
        result = validate_satellite(
            SP_LAT, SP_LNG, CAPACITY_KW, NOON_UTC,
            reported_kwh=30.0, provider=provider,
        )
        assert result.satellite_pass is False
        assert result.low_irradiance is True
        assert result.high_generation_low_sun is True

    def test_low_ghi_zero_generation_passes(self):
        """GHI=100 + 0 kWh → pass (inversor desligado válido)."""
        provider = MockSatelliteProvider(fixed_ghi_wm2=100.0, fixed_cloud_cover_pct=85.0)
        result = validate_satellite(
            SP_LAT, SP_LNG, CAPACITY_KW, NOON_UTC,
            reported_kwh=0.0, provider=provider,
        )
        assert result.satellite_pass is True
        assert result.high_generation_low_sun is False

    def test_low_ghi_small_generation_no_severe_flag(self):
        """GHI=100 + 2 kWh → below high_gen_threshold (7.5) → no severe flag."""
        provider = MockSatelliteProvider(fixed_ghi_wm2=100.0, fixed_cloud_cover_pct=85.0)
        result = validate_satellite(
            SP_LAT, SP_LNG, CAPACITY_KW, NOON_UTC,
            reported_kwh=2.0, provider=provider,
        )
        assert result.low_irradiance is True
        assert result.high_generation_low_sun is False  # 2 < 7.5

    # ── GHI zero (noite / eclipse total) ─────────────────────────

    def test_zero_ghi_any_generation_fails(self):
        """GHI=0 + qualquer kWh positivo → fail."""
        provider = MockSatelliteProvider(fixed_ghi_wm2=0.0, fixed_cloud_cover_pct=100.0)
        result = validate_satellite(
            SP_LAT, SP_LNG, CAPACITY_KW, NOON_UTC,
            reported_kwh=1.0, provider=provider,
        )
        assert result.satellite_pass is False
        assert result.satellite_max_kwh == 0.0

    def test_zero_ghi_zero_generation_passes(self):
        """GHI=0 + 0 kWh → pass."""
        provider = MockSatelliteProvider(fixed_ghi_wm2=0.0, fixed_cloud_cover_pct=100.0)
        result = validate_satellite(
            SP_LAT, SP_LNG, CAPACITY_KW, NOON_UTC,
            reported_kwh=0.0, provider=provider,
        )
        assert result.satellite_pass is True

    # ── Boundary ──────────────────────────────────────────────────

    def test_energy_at_exact_satellite_max_passes(self):
        """Energia = satellite_max → pass (<=)."""
        provider = MockSatelliteProvider(fixed_ghi_wm2=500.0, fixed_cloud_cover_pct=30.0)
        # sat_max = 75 × (500/1000) × 1.2 = 45.0
        result = validate_satellite(
            SP_LAT, SP_LNG, CAPACITY_KW, NOON_UTC,
            reported_kwh=45.0, provider=provider,
        )
        assert result.satellite_pass is True
        assert result.satellite_max_kwh == pytest.approx(45.0, abs=0.01)

    def test_energy_just_above_satellite_max_fails(self):
        """Energia 0.01 acima do satellite_max → fail."""
        provider = MockSatelliteProvider(fixed_ghi_wm2=500.0, fixed_cloud_cover_pct=30.0)
        result = validate_satellite(
            SP_LAT, SP_LNG, CAPACITY_KW, NOON_UTC,
            reported_kwh=45.01, provider=provider,
        )
        assert result.satellite_pass is False

    # ── Result completeness ──────────────────────────────────────

    def test_result_has_all_fields(self):
        provider = MockSatelliteProvider(fixed_ghi_wm2=600.0, fixed_cloud_cover_pct=20.0)
        result = validate_satellite(
            SP_LAT, SP_LNG, CAPACITY_KW, NOON_UTC,
            reported_kwh=10.0, provider=provider,
        )
        assert isinstance(result, SatelliteValidationResult)
        assert isinstance(result.satellite_ghi_wm2, float)
        assert isinstance(result.satellite_source, str)
        assert isinstance(result.satellite_max_kwh, float)
        assert isinstance(result.satellite_pass, bool)
        assert isinstance(result.cloud_cover_pct, float)
        assert isinstance(result.low_irradiance, bool)
        assert isinstance(result.high_generation_low_sun, bool)
        assert isinstance(result.confidence, float)
        assert result.satellite_source == "mock"


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — Provider injection
# ═══════════════════════════════════════════════════════════════════

class TestProviderInjection:
    """Verifica set/get/reset do provider global."""

    def test_set_and_get_provider(self):
        custom = MockSatelliteProvider(fixed_ghi_wm2=999.0)
        set_satellite_provider(custom)
        assert get_satellite_provider() is custom
        reset_satellite_provider()

    def test_reset_restores_default(self):
        custom = MockSatelliteProvider(fixed_ghi_wm2=999.0)
        set_satellite_provider(custom)
        reset_satellite_provider()
        provider = get_satellite_provider()
        assert isinstance(provider, MockSatelliteProvider)
        assert provider is not custom

    def test_validate_uses_global_provider(self):
        set_satellite_provider(MockSatelliteProvider(fixed_ghi_wm2=123.0))
        result = validate_satellite(
            SP_LAT, SP_LNG, CAPACITY_KW, NOON_UTC, reported_kwh=0.0
        )
        assert result.satellite_ghi_wm2 == 123.0
        reset_satellite_provider()

    def test_validate_uses_explicit_provider_over_global(self):
        set_satellite_provider(MockSatelliteProvider(fixed_ghi_wm2=999.0))
        explicit = MockSatelliteProvider(fixed_ghi_wm2=111.0)
        result = validate_satellite(
            SP_LAT, SP_LNG, CAPACITY_KW, NOON_UTC,
            reported_kwh=0.0, provider=explicit,
        )
        assert result.satellite_ghi_wm2 == 111.0  # Explicit wins
        reset_satellite_provider()


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — POST /telemetry com satellite mock injetado
# ═══════════════════════════════════════════════════════════════════

class TestSatelliteIntegrationAccepted:
    """Satélite + geração compatível → accepted, satellite_pass=True."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        set_server_now_fn(_fixed_clock(NOON_UTC))
        # GHI alto (céu limpo) → sat_max ~ 72 kWh
        set_satellite_provider(
            MockSatelliteProvider(fixed_ghi_wm2=800.0, fixed_cloud_cover_pct=10.0)
        )
        yield
        reset_server_now_fn()
        reset_satellite_provider()

    def test_low_energy_accepted(self, client, ecdsa_keys):
        """12 kWh com GHI=800 → accepted + satellite_pass=True."""
        private_pem, public_pem = ecdsa_keys
        ts_str = NOON_UTC.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = _make_sat_payload(private_pem, public_pem, ts_str, energy_kwh=12.0)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        data = r.json()

        assert data["status"] == "accepted"
        assert data["satellite_pass"] is True
        assert data["satellite_ghi_wm2"] == 800.0
        assert data["satellite_max_kwh"] > 12.0
        assert data["cloud_cover_pct"] == 10.0
        assert data["confidence_score"] == 100.0

    def test_zero_energy_accepted(self, client, ecdsa_keys):
        """0 kWh → always valid."""
        private_pem, public_pem = ecdsa_keys
        ts_str = NOON_UTC.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = _make_sat_payload(private_pem, public_pem, ts_str, energy_kwh=0.0)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        assert r.json()["satellite_pass"] is True


class TestSatelliteIntegrationReview:
    """Satélite detecta inconsistência → review, satellite_pass=False."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        set_server_now_fn(_fixed_clock(NOON_UTC))
        yield
        reset_server_now_fn()
        reset_satellite_provider()

    def test_exceeds_satellite_max_review(self, client, ecdsa_keys):
        """100 kWh com GHI=500 → sat_max=45 → excede → review."""
        set_satellite_provider(
            MockSatelliteProvider(fixed_ghi_wm2=500.0, fixed_cloud_cover_pct=30.0)
        )
        private_pem, public_pem = ecdsa_keys
        ts_str = NOON_UTC.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = _make_sat_payload(private_pem, public_pem, ts_str, energy_kwh=100.0)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        data = r.json()

        assert data["satellite_pass"] is False
        # Physics may also fail (100 > theoretical), so just check satellite
        assert data["satellite_max_kwh"] < 100.0
        assert data["confidence_score"] < 100.0

    def test_low_ghi_high_gen_severe_flag(self, client, ecdsa_keys):
        """GHI=80 + 30 kWh → low irradiance + high generation → satellite fail."""
        set_satellite_provider(
            MockSatelliteProvider(fixed_ghi_wm2=80.0, fixed_cloud_cover_pct=90.0)
        )
        private_pem, public_pem = ecdsa_keys
        ts_str = NOON_UTC.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = _make_sat_payload(private_pem, public_pem, ts_str, energy_kwh=30.0)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        data = r.json()

        assert data["satellite_pass"] is False
        assert data["cloud_cover_pct"] == 90.0
        # Score: sig=20, ntp=20, phys=30, sat=0, cons=15 = 85 → REVIEW
        assert data["confidence_score"] == 85.0
        assert data["status"] == "review"

    def test_zero_ghi_any_generation_review(self, client, ecdsa_keys):
        """GHI=0 + 5 kWh → impossible → review."""
        set_satellite_provider(
            MockSatelliteProvider(fixed_ghi_wm2=0.0, fixed_cloud_cover_pct=100.0)
        )
        private_pem, public_pem = ecdsa_keys
        ts_str = NOON_UTC.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = _make_sat_payload(private_pem, public_pem, ts_str, energy_kwh=5.0)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        data = r.json()

        assert data["satellite_pass"] is False
        assert data["satellite_max_kwh"] == 0.0

    def test_validation_record_has_satellite_data(self, client, ecdsa_keys, db_session):
        """Validation record persiste satellite_ghi, satellite_pass, cloud_cover."""
        set_satellite_provider(
            MockSatelliteProvider(fixed_ghi_wm2=300.0, fixed_cloud_cover_pct=60.0)
        )
        private_pem, public_pem = ecdsa_keys
        ts_str = NOON_UTC.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = _make_sat_payload(private_pem, public_pem, ts_str, energy_kwh=50.0)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        data = r.json()

        from app.models.models import Validation as Val
        val = db_session.query(Val).filter(
            Val.validation_id == data["validation_id"]
        ).first()

        assert val is not None
        assert float(val.satellite_ghi_wm2) == 300.0
        assert val.satellite_source == "mock"
        # sat_max = 75 × 0.3 × 1.2 = 27 → 50 > 27 → fail
        assert val.satellite_pass is False
        assert float(val.satellite_max_kwh) == pytest.approx(27.0, abs=0.1)
        assert float(val.cloud_cover_pct) == 60.0

    def test_confidence_score_combined_failures(self, client, ecdsa_keys):
        """NTP fail + satellite fail → score 65 → REJECTED."""
        set_satellite_provider(
            MockSatelliteProvider(fixed_ghi_wm2=200.0, fixed_cloud_cover_pct=70.0)
        )
        private_pem, public_pem = ecdsa_keys
        # Inject NTP drift (10ms = fail)
        ntp_shifted = NOON_UTC + timedelta(milliseconds=10)
        set_server_now_fn(_fixed_clock(ntp_shifted))
        ts_str = NOON_UTC.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = _make_sat_payload(private_pem, public_pem, ts_str, energy_kwh=30.0)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        data = r.json()

        assert data["ntp_pass"] is False
        assert data["satellite_pass"] is False
        # score = 20(sig) + 0(ntp) + 30(phys) + 0(sat) + 15(cons) = 65 → REJECTED
        assert data["confidence_score"] == 65.0
        assert data["status"] == "rejected"

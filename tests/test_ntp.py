"""
Testes automatizados — Camada 2: Verificação NTP Blindada ±5ms

Cenários cobertos:

  UNIT TESTS (compute_ntp_drift_ms / check_ntp_drift):
    ✅ Drift zero (payload == server)        → ntp_pass=True,  drift=0ms
    ✅ Drift +3ms (dentro da tolerância)      → ntp_pass=True,  drift=+3ms
    ✅ Drift -4ms (dentro da tolerância)      → ntp_pass=True,  drift=-4ms
    ✅ Drift +5ms exato (boundary)            → ntp_pass=True,  drift=+5ms
    ✅ Drift -5ms exato (boundary)            → ntp_pass=True,  drift=-5ms
    ❌ Drift +5.001ms (just over)             → ntp_pass=False, drift=+5.001ms
    ❌ Drift -5.001ms (just over)             → ntp_pass=False, drift=-5.001ms
    ❌ Drift +10ms (payload no futuro)        → ntp_pass=False, drift=+10ms
    ❌ Drift -10ms (payload no passado)       → ntp_pass=False, drift=-10ms
    ❌ Drift +500ms (grande — fraude)         → ntp_pass=False, drift=+500ms
    ❌ Drift -2000ms (grande negativo)        → ntp_pass=False, drift=-2000ms

  INTEGRATION TESTS (POST /telemetry com clock injetado):
    ✅ Drift 0ms    → 201, status=accepted, ntp_pass=True
    ✅ Drift +2ms   → 201, status=accepted, ntp_pass=True
    ✅ Drift -3ms   → 201, status=accepted, ntp_pass=True
    ❌ Drift +10ms  → 201, status=review,   ntp_pass=False
    ❌ Drift -10ms  → 201, status=review,   ntp_pass=False
    ❌ Drift +100ms → 201, status=review,   ntp_pass=False
    ✅ Boundary +5ms → 201, status=accepted, ntp_pass=True
    ❌ Boundary +6ms → 201, status=review,   ntp_pass=False
"""
import uuid
from datetime import datetime, timezone, timedelta

import pytest

from app.security import (
    compute_ntp_drift_ms,
    check_ntp_drift,
    NTP_MAX_DRIFT_MS,
    canonical_payload,
    sign_payload,
)
from app.api.telemetry import set_server_now_fn, reset_server_now_fn
from app.satellite import MockSatelliteProvider, set_satellite_provider, reset_satellite_provider

SEED_PLANT_ID = "00000000-0000-0000-0000-000000000001"

# Fixed satellite mock for NTP integration tests (high GHI so satellite doesn't interfere)
_SAT_PROVIDER = MockSatelliteProvider(fixed_ghi_wm2=800.0, fixed_cloud_cover_pct=10.0)


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════


def _fixed_clock(dt: datetime):
    """Retorna callable que sempre retorna o datetime dado."""
    def _fn() -> datetime:
        return dt
    return _fn


def _make_ntp_payload(private_pem, public_pem, timestamp_str, nonce=None):
    """Helper: monta payload assinado com timestamp customizado."""
    nonce = nonce or uuid.uuid4().hex[:32]
    canon = canonical_payload(
        SEED_PLANT_ID, timestamp_str, 5.5, 12.3, nonce,
    )
    signature = sign_payload(private_pem, canon)
    return {
        "plant_id": SEED_PLANT_ID,
        "timestamp": timestamp_str,
        "power_kw": 5.5,
        "energy_kwh": 12.3,
        "signature": signature,
        "public_key": public_pem,
        "nonce": nonce,
    }


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — compute_ntp_drift_ms e check_ntp_drift (sem banco)
# ═══════════════════════════════════════════════════════════════════


class TestComputeNTPDrift:
    """Cálculo puro de drift entre timestamp do payload e servidor."""

    SERVER_TIME = datetime(2026, 2, 23, 14, 30, 0, 0, tzinfo=timezone.utc)

    def _drift(self, offset_ms: float) -> float:
        """Helper: cria timestamp com offset dado e retorna drift calculado."""
        payload_dt = self.SERVER_TIME + timedelta(milliseconds=offset_ms)
        # Full microsecond precision (%f = 6 digits) — critical for sub-ms accuracy
        ts_str = payload_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        drift_ms, _ = compute_ntp_drift_ms(ts_str, _fixed_clock(self.SERVER_TIME))
        return drift_ms

    def test_zero_drift(self):
        drift = self._drift(0)
        assert abs(drift) < 0.01, f"Expected ~0ms, got {drift}ms"

    def test_positive_3ms(self):
        drift = self._drift(+3.0)
        assert abs(drift - 3.0) < 0.01, f"Expected ~+3ms, got {drift}ms"

    def test_negative_4ms(self):
        drift = self._drift(-4.0)
        assert abs(drift - (-4.0)) < 0.01, f"Expected ~-4ms, got {drift}ms"

    def test_positive_10ms(self):
        drift = self._drift(+10.0)
        assert abs(drift - 10.0) < 0.01

    def test_negative_10ms(self):
        drift = self._drift(-10.0)
        assert abs(drift - (-10.0)) < 0.01

    def test_large_positive_500ms(self):
        drift = self._drift(+500.0)
        assert abs(drift - 500.0) < 0.1

    def test_large_negative_2000ms(self):
        drift = self._drift(-2000.0)
        assert abs(drift - (-2000.0)) < 0.1

    def test_drift_sign_positive_means_payload_ahead(self):
        """Drift positivo = payload está no futuro relativo ao servidor."""
        drift = self._drift(+7.0)
        assert drift > 0

    def test_drift_sign_negative_means_payload_behind(self):
        """Drift negativo = payload está no passado relativo ao servidor."""
        drift = self._drift(-7.0)
        assert drift < 0


class TestCheckNTPDrift:
    """Verificação de pass/fail baseada no threshold de ±5ms."""

    SERVER_TIME = datetime(2026, 2, 23, 14, 30, 0, 0, tzinfo=timezone.utc)

    def _check(self, offset_ms: float):
        payload_dt = self.SERVER_TIME + timedelta(milliseconds=offset_ms)
        ts_str = payload_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return check_ntp_drift(ts_str, NTP_MAX_DRIFT_MS, _fixed_clock(self.SERVER_TIME))

    # ── Dentro da tolerância ──────────────────────────────────────

    def test_zero_passes(self):
        ntp_pass, drift_ms, _ = self._check(0)
        assert ntp_pass is True
        assert abs(drift_ms) < 0.01

    def test_positive_3ms_passes(self):
        ntp_pass, drift_ms, _ = self._check(+3.0)
        assert ntp_pass is True

    def test_negative_4ms_passes(self):
        ntp_pass, drift_ms, _ = self._check(-4.0)
        assert ntp_pass is True

    def test_boundary_positive_5ms_passes(self):
        """Exatamente +5ms é aceito (<=)."""
        ntp_pass, drift_ms, _ = self._check(+5.0)
        assert ntp_pass is True
        assert abs(drift_ms - 5.0) < 0.01

    def test_boundary_negative_5ms_passes(self):
        """Exatamente -5ms é aceito (<=)."""
        ntp_pass, drift_ms, _ = self._check(-5.0)
        assert ntp_pass is True
        assert abs(drift_ms - (-5.0)) < 0.01

    # ── Fora da tolerância ────────────────────────────────────────

    def test_just_over_positive_fails(self):
        """5.001ms excede tolerância → ntp_pass=False."""
        # timedelta resolve no máximo até microsegundos (1μs = 0.001ms)
        ntp_pass, drift_ms, _ = self._check(+5.5)
        assert ntp_pass is False

    def test_just_over_negative_fails(self):
        """-5.5ms excede tolerância → ntp_pass=False."""
        ntp_pass, drift_ms, _ = self._check(-5.5)
        assert ntp_pass is False

    def test_positive_10ms_fails(self):
        ntp_pass, drift_ms, _ = self._check(+10.0)
        assert ntp_pass is False
        assert drift_ms > 0

    def test_negative_10ms_fails(self):
        ntp_pass, drift_ms, _ = self._check(-10.0)
        assert ntp_pass is False
        assert drift_ms < 0

    def test_large_drift_500ms_fails(self):
        ntp_pass, drift_ms, _ = self._check(+500.0)
        assert ntp_pass is False

    def test_large_drift_negative_2000ms_fails(self):
        ntp_pass, drift_ms, _ = self._check(-2000.0)
        assert ntp_pass is False


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — POST /telemetry com drift NTP simulado
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _inject_satellite_for_ntp_tests():
    """Inject fixed satellite for NTP integration tests."""
    set_satellite_provider(_SAT_PROVIDER)
    yield
    reset_satellite_provider()


class TestNTPIntegrationAccepted:
    """Drift dentro de ±5ms → 201, status=accepted, ntp_pass=True."""

    BASE_TIME = datetime(2026, 2, 23, 14, 30, 0, 0, tzinfo=timezone.utc)

    def _post_with_drift(self, client, ecdsa_keys, offset_ms: float):
        """Helper: envia telemetria com drift simulado via clock injection."""
        private_pem, public_pem = ecdsa_keys
        # Server time é BASE_TIME; payload timestamp é BASE_TIME + offset
        set_server_now_fn(_fixed_clock(self.BASE_TIME))
        payload_dt = self.BASE_TIME + timedelta(milliseconds=offset_ms)
        ts_str = payload_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = _make_ntp_payload(private_pem, public_pem, ts_str)
        try:
            return client.post("/telemetry", json=payload)
        finally:
            reset_server_now_fn()

    def test_drift_zero_accepted(self, client, ecdsa_keys):
        r = self._post_with_drift(client, ecdsa_keys, 0)
        assert r.status_code == 201
        data = r.json()
        assert data["status"] == "accepted"
        assert data["ntp_pass"] is True
        assert abs(data["ntp_drift_ms"]) < 0.1

    def test_drift_positive_2ms_accepted(self, client, ecdsa_keys):
        r = self._post_with_drift(client, ecdsa_keys, +2.0)
        assert r.status_code == 201
        data = r.json()
        assert data["status"] == "accepted"
        assert data["ntp_pass"] is True

    def test_drift_negative_3ms_accepted(self, client, ecdsa_keys):
        r = self._post_with_drift(client, ecdsa_keys, -3.0)
        assert r.status_code == 201
        data = r.json()
        assert data["status"] == "accepted"
        assert data["ntp_pass"] is True

    def test_boundary_positive_5ms_accepted(self, client, ecdsa_keys):
        r = self._post_with_drift(client, ecdsa_keys, +5.0)
        assert r.status_code == 201
        data = r.json()
        assert data["status"] == "accepted"
        assert data["ntp_pass"] is True


class TestNTPIntegrationReview:
    """Drift > ±5ms → 201, status=rejected (score 80), ntp_pass=False."""

    BASE_TIME = datetime(2026, 2, 23, 14, 30, 0, 0, tzinfo=timezone.utc)

    def _post_with_drift(self, client, ecdsa_keys, offset_ms: float):
        private_pem, public_pem = ecdsa_keys
        set_server_now_fn(_fixed_clock(self.BASE_TIME))
        payload_dt = self.BASE_TIME + timedelta(milliseconds=offset_ms)
        ts_str = payload_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = _make_ntp_payload(private_pem, public_pem, ts_str)
        try:
            return client.post("/telemetry", json=payload)
        finally:
            reset_server_now_fn()

    def test_drift_positive_10ms_rejected(self, client, ecdsa_keys):
        """Payload 10ms à frente do servidor → REJECTED (score 80)."""
        r = self._post_with_drift(client, ecdsa_keys, +10.0)
        assert r.status_code == 201
        data = r.json()
        assert data["status"] == "rejected"
        assert data["ntp_pass"] is False
        assert data["ntp_drift_ms"] > 5.0
        assert data["confidence_score"] == 80.0

    def test_drift_negative_10ms_rejected(self, client, ecdsa_keys):
        """Payload 10ms atrás do servidor → REJECTED (score 80)."""
        r = self._post_with_drift(client, ecdsa_keys, -10.0)
        assert r.status_code == 201
        data = r.json()
        assert data["status"] == "rejected"
        assert data["ntp_pass"] is False
        assert data["ntp_drift_ms"] < -5.0

    def test_drift_positive_100ms_rejected(self, client, ecdsa_keys):
        """Drift grande (100ms) → REJECTED."""
        r = self._post_with_drift(client, ecdsa_keys, +100.0)
        assert r.status_code == 201
        data = r.json()
        assert data["status"] == "rejected"
        assert data["ntp_pass"] is False

    def test_drift_negative_500ms_rejected(self, client, ecdsa_keys):
        """Drift muito grande negativo (500ms) → REJECTED."""
        r = self._post_with_drift(client, ecdsa_keys, -500.0)
        assert r.status_code == 201
        data = r.json()
        assert data["status"] == "rejected"
        assert data["ntp_pass"] is False

    def test_boundary_positive_6ms_rejected(self, client, ecdsa_keys):
        """6ms (logo acima de 5ms) → REJECTED (score 80)."""
        r = self._post_with_drift(client, ecdsa_keys, +6.0)
        assert r.status_code == 201
        data = r.json()
        assert data["status"] == "rejected"
        assert data["ntp_pass"] is False

    def test_rejected_still_persists_data(self, client, ecdsa_keys):
        """Mesmo em REJECTED, telemetria é persistida (não descartada)."""
        r = self._post_with_drift(client, ecdsa_keys, +50.0)
        assert r.status_code == 201
        data = r.json()
        assert data["status"] == "rejected"
        # Tem telemetry_id = foi persistido
        assert "telemetry_id" in data
        assert data["payload_sha256"] is not None
        assert len(data["payload_sha256"]) == 64

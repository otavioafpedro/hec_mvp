"""
Testes automatizados — POST /telemetry

Cenários cobertos:
  ✅ Assinatura ECDSA válida → 201 accepted
  ❌ Assinatura ECDSA inválida → 401 rejected
  ❌ Replay attack (nonce repetido) → 409 rejected
  ✅ SHA-256 do payload verificável
  ❌ Planta inexistente → 404
  ❌ Nonce curto demais → 422

Nota: Testes de NTP drift estão em test_ntp.py
"""
import uuid
from datetime import datetime, timezone, timedelta

import pytest

from app.security import (
    canonical_payload,
    sha256_hash,
    sign_payload,
    verify_ecdsa_signature,
    generate_ecdsa_keypair,
)
from app.api.telemetry import set_server_now_fn, reset_server_now_fn
from app.satellite import MockSatelliteProvider, set_satellite_provider, reset_satellite_provider

SEED_PLANT_ID = "00000000-0000-0000-0000-000000000001"

# Timestamp fixo dos testes — injetamos relógio do servidor para coincidir
TEST_TIMESTAMP = "2026-02-23T14:30:00Z"
TEST_SERVER_TIME = datetime(2026, 2, 23, 14, 30, 0, tzinfo=timezone.utc)

# Satellite mock fixo — GHI alto para que testes de ECDSA/NTP não falhem por satélite
_SAT_PROVIDER = MockSatelliteProvider(fixed_ghi_wm2=800.0, fixed_cloud_cover_pct=10.0)


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — Módulo de Segurança (sem banco)
# ═══════════════════════════════════════════════════════════════════


class TestCanonicalPayload:
    """Payload canônico deve ser determinístico e ordenado."""

    def test_deterministic(self):
        a = canonical_payload(SEED_PLANT_ID, "2026-02-23T14:00:00Z", 5.5, 12.3, "nonce123")
        b = canonical_payload(SEED_PLANT_ID, "2026-02-23T14:00:00Z", 5.5, 12.3, "nonce123")
        assert a == b

    def test_sorted_keys(self):
        canon = canonical_payload(SEED_PLANT_ID, "2026-02-23T14:00:00Z", 5.5, 12.3, "nonce123")
        # Keys devem estar em ordem alfabética
        assert '"energy_kwh"' in canon
        assert canon.index('"energy_kwh"') < canon.index('"nonce"')
        assert canon.index('"nonce"') < canon.index('"plant_id"')
        assert canon.index('"plant_id"') < canon.index('"power_kw"')
        assert canon.index('"power_kw"') < canon.index('"timestamp"')

    def test_different_nonce_different_payload(self):
        a = canonical_payload(SEED_PLANT_ID, "2026-02-23T14:00:00Z", 5.5, 12.3, "nonce_A")
        b = canonical_payload(SEED_PLANT_ID, "2026-02-23T14:00:00Z", 5.5, 12.3, "nonce_B")
        assert a != b


class TestSHA256:
    """SHA-256 deve gerar hash consistente e de 64 chars hex."""

    def test_hash_length(self):
        h = sha256_hash("test payload")
        assert len(h) == 64

    def test_deterministic(self):
        assert sha256_hash("abc") == sha256_hash("abc")

    def test_different_inputs(self):
        assert sha256_hash("abc") != sha256_hash("abd")


class TestECDSAVerification:
    """Validação de assinatura ECDSA secp256k1."""

    def test_valid_signature(self, ecdsa_keys):
        private_pem, public_pem = ecdsa_keys
        message = "test message for signing"

        signature_hex = sign_payload(private_pem, message)
        assert verify_ecdsa_signature(public_pem, signature_hex, message) is True

    def test_invalid_signature_wrong_message(self, ecdsa_keys):
        private_pem, public_pem = ecdsa_keys

        signature_hex = sign_payload(private_pem, "original message")
        assert verify_ecdsa_signature(public_pem, signature_hex, "tampered message") is False

    def test_invalid_signature_wrong_key(self, ecdsa_keys):
        private_pem, _ = ecdsa_keys
        _, other_public_pem = generate_ecdsa_keypair()

        message = "test message"
        signature_hex = sign_payload(private_pem, message)
        # Verificar com chave pública DIFERENTE → deve falhar
        assert verify_ecdsa_signature(other_public_pem, signature_hex, message) is False

    def test_invalid_signature_garbage(self, ecdsa_keys):
        _, public_pem = ecdsa_keys
        assert verify_ecdsa_signature(public_pem, "deadbeef" * 8, "message") is False

    def test_invalid_pem(self):
        assert verify_ecdsa_signature("not-a-pem", "aabb", "msg") is False


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — POST /telemetry (com banco de teste)
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _inject_server_clock():
    """Injeta relógio fixo e satellite mock para testes existentes."""
    set_server_now_fn(lambda: TEST_SERVER_TIME)
    set_satellite_provider(_SAT_PROVIDER)
    yield
    reset_server_now_fn()
    reset_satellite_provider()


def _make_signed_payload(private_pem: str, public_pem: str, nonce: str = None, **overrides):
    """Helper: monta payload assinado para testes."""
    plant_id = overrides.get("plant_id", SEED_PLANT_ID)
    timestamp = overrides.get("timestamp", TEST_TIMESTAMP)
    power_kw = overrides.get("power_kw", 5.5)
    energy_kwh = overrides.get("energy_kwh", 12.3)
    nonce = nonce or uuid.uuid4().hex[:32]

    canon = canonical_payload(plant_id, timestamp, power_kw, energy_kwh, nonce)
    signature = sign_payload(private_pem, canon)

    return {
        "plant_id": plant_id,
        "timestamp": timestamp,
        "power_kw": power_kw,
        "energy_kwh": energy_kwh,
        "signature": signature,
        "public_key": public_pem,
        "nonce": nonce,
    }


class TestTelemetryEndpointSuccess:
    """✅ Cenário de sucesso — ECDSA + NTP + física + satélite ok."""

    def test_valid_telemetry_returns_201(self, client, ecdsa_keys):
        private_pem, public_pem = ecdsa_keys
        payload = _make_signed_payload(private_pem, public_pem)

        response = client.post("/telemetry", json=payload)

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "accepted"
        assert data["plant_id"] == SEED_PLANT_ID
        assert len(data["payload_sha256"]) == 64
        assert "telemetry_id" in data
        # NTP (clock injected = drift ~0)
        assert data["ntp_pass"] is True
        # Physics (12.3 kWh well below max for 75kWp at noon)
        assert data["physics_pass"] is True
        assert data["theoretical_max_kwh"] > 0
        # Satellite (mock fixed GHI=800 → max ~72 kWh, 12.3 passes)
        assert data["satellite_pass"] is True
        assert data["satellite_ghi_wm2"] == 800.0
        assert data["satellite_max_kwh"] > 12.3
        # Consolidated
        assert data["validation_id"] is not None
        assert data["confidence_score"] == 100.0
        # Breakdown (official weights: sig=20, ntp=20, phys=30, sat=15, cons=15)
        bd = data["confidence_breakdown"]
        assert bd["C1_signature"] == 20
        assert bd["C2_ntp"] == 20
        assert bd["C3_physics"] == 30
        assert bd["C4_satellite"] == 15
        assert bd["C5_consensus"] == 15

    def test_sha256_is_verifiable(self, client, ecdsa_keys):
        private_pem, public_pem = ecdsa_keys
        nonce = uuid.uuid4().hex[:32]
        payload = _make_signed_payload(private_pem, public_pem, nonce=nonce)

        response = client.post("/telemetry", json=payload)
        assert response.status_code == 201

        # Recalcular SHA-256 localmente e comparar
        canon = canonical_payload(
            SEED_PLANT_ID, TEST_TIMESTAMP, 5.5, 12.3, nonce
        )
        expected_hash = sha256_hash(canon)
        assert response.json()["payload_sha256"] == expected_hash

    def test_optional_fields(self, client, ecdsa_keys):
        private_pem, public_pem = ecdsa_keys
        payload = _make_signed_payload(private_pem, public_pem)
        payload["voltage_v"] = 220.5
        payload["temperature_c"] = 35.2
        payload["irradiance_wm2"] = 850.0

        response = client.post("/telemetry", json=payload)
        assert response.status_code == 201


class TestTelemetryEndpointInvalidSignature:
    """❌ Assinatura ECDSA inválida → 401."""

    def test_tampered_signature(self, client, ecdsa_keys):
        private_pem, public_pem = ecdsa_keys
        payload = _make_signed_payload(private_pem, public_pem)
        # Corromper assinatura
        payload["signature"] = "ff" + payload["signature"][2:]

        response = client.post("/telemetry", json=payload)

        assert response.status_code == 401
        assert "inválida" in response.json()["detail"].lower() or "ECDSA" in response.json()["detail"]

    def test_wrong_key(self, client, ecdsa_keys):
        private_pem, _ = ecdsa_keys
        _, other_public_pem = generate_ecdsa_keypair()

        payload = _make_signed_payload(private_pem, other_public_pem)
        # Assinatura feita com private_pem, mas public_key é de OUTRO par

        response = client.post("/telemetry", json=payload)
        assert response.status_code == 401

    def test_garbage_signature(self, client, ecdsa_keys):
        _, public_pem = ecdsa_keys
        payload = _make_signed_payload(*ecdsa_keys)
        payload["signature"] = "deadbeefdeadbeef" * 4

        response = client.post("/telemetry", json=payload)
        assert response.status_code == 401


class TestTelemetryEndpointReplayAttack:
    """❌ Replay attack — nonce repetido → 409."""

    def test_same_nonce_rejected(self, client, ecdsa_keys):
        private_pem, public_pem = ecdsa_keys
        fixed_nonce = "replay_test_nonce_12345678"

        # Primeira requisição → aceita
        payload1 = _make_signed_payload(private_pem, public_pem, nonce=fixed_nonce)
        r1 = client.post("/telemetry", json=payload1)
        assert r1.status_code == 201

        # Segunda requisição com MESMO nonce → rejeitada
        payload2 = _make_signed_payload(private_pem, public_pem, nonce=fixed_nonce)
        r2 = client.post("/telemetry", json=payload2)
        assert r2.status_code == 409
        assert "replay" in r2.json()["detail"].lower() or "nonce" in r2.json()["detail"].lower()

    def test_different_nonces_both_accepted(self, client, ecdsa_keys):
        private_pem, public_pem = ecdsa_keys

        payload1 = _make_signed_payload(private_pem, public_pem, nonce="unique_nonce_aaaa1234")
        payload2 = _make_signed_payload(private_pem, public_pem, nonce="unique_nonce_bbbb5678")

        r1 = client.post("/telemetry", json=payload1)
        r2 = client.post("/telemetry", json=payload2)

        assert r1.status_code == 201
        assert r2.status_code == 201


class TestTelemetryEndpointEdgeCases:
    """Cenários de borda."""

    def test_plant_not_found(self, client, ecdsa_keys):
        private_pem, public_pem = ecdsa_keys
        fake_plant_id = str(uuid.uuid4())
        payload = _make_signed_payload(private_pem, public_pem, plant_id=fake_plant_id)

        response = client.post("/telemetry", json=payload)
        assert response.status_code == 404

    def test_nonce_too_short(self, client, ecdsa_keys):
        private_pem, public_pem = ecdsa_keys
        payload = _make_signed_payload(private_pem, public_pem, nonce="short")

        response = client.post("/telemetry", json=payload)
        assert response.status_code == 422  # Pydantic validation

    def test_negative_power_rejected(self, client, ecdsa_keys):
        private_pem, public_pem = ecdsa_keys
        payload = _make_signed_payload(private_pem, public_pem, power_kw=-5.0)

        response = client.post("/telemetry", json=payload)
        assert response.status_code == 422

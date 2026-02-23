"""
Testes automatizados — HEC Certificate Generator

Cenários cobertos:

  UNIT TESTS — build_certificate_json:
    ✅ JSON tem todas as seções (certificate, plant, energy, validation, issuance)
    ✅ Campos corretos da planta
    ✅ Campos corretos de energia
    ✅ Campos de validação com 5 camadas
    ✅ Status issuance = pending

  UNIT TESTS — compute_certificate_hash:
    ✅ Hash é SHA-256 válido (64 hex chars)
    ✅ Hash é determinístico (mesmo JSON → mesmo hash)
    ✅ JSON diferente → hash diferente
    ✅ Ordem das chaves não afeta hash (sort_keys=True)

  UNIT TESTS — generate_certificate_pdf:
    ✅ PDF gerado é bytes válido
    ✅ PDF começa com %PDF header
    ✅ PDF tem tamanho razoável (> 1KB)

  UNIT TESTS — issue_hec:
    ✅ Validação APPROVED → emite com status=pending
    ✅ Validação REVIEW → ValueError
    ✅ Validação REJECTED → ValueError
    ✅ HEC record criado no DB com hash e JSON

  INTEGRATION TESTS — POST /hec/issue:
    ✅ Emite HEC para validação APPROVED
    ✅ 409 se HEC já existe para validação
    ✅ 422 se validação não é APPROVED
    ✅ 404 se validação não existe

  INTEGRATION TESTS — GET /hec/{hec_id}:
    ✅ Retorna certificado existente
    ✅ 404 para ID inexistente

  INTEGRATION TESTS — Auto-issue via POST /telemetry:
    ✅ APPROVED → auto-issue HEC, response inclui hec_id
    ✅ REJECTED → sem HEC, hec_id = null
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone, timedelta

import pytest

from app.hec_generator import (
    build_certificate_json,
    compute_certificate_hash,
    generate_certificate_pdf,
    issue_hec,
    HECIssuanceResult,
)
from app.models.models import Plant, Validation, HECCertificate
from app.security import canonical_payload, sign_payload
from app.api.telemetry import set_server_now_fn, reset_server_now_fn
from app.satellite import MockSatelliteProvider, set_satellite_provider, reset_satellite_provider


SEED_PLANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
NOON_UTC = datetime(2026, 2, 23, 14, 30, 0, 0, tzinfo=timezone.utc)
ISSUED_AT = datetime(2026, 2, 23, 15, 0, 0, 0, tzinfo=timezone.utc)
_SAT_PROVIDER = MockSatelliteProvider(fixed_ghi_wm2=800.0, fixed_cloud_cover_pct=10.0)


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def _fixed_clock(dt):
    return lambda: dt


def _make_payload(private_pem, public_pem, energy_kwh=12.3, nonce=None):
    nonce = nonce or uuid.uuid4().hex[:32]
    ts_str = NOON_UTC.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    canon = canonical_payload(str(SEED_PLANT_ID), ts_str, 5.5, energy_kwh, nonce)
    signature = sign_payload(private_pem, canon)
    return {
        "plant_id": str(SEED_PLANT_ID),
        "timestamp": ts_str,
        "power_kw": 5.5,
        "energy_kwh": energy_kwh,
        "signature": signature,
        "public_key": public_pem,
        "nonce": nonce,
    }


def _make_approved_validation(db_session):
    """Create an APPROVED validation record for testing."""
    plant = db_session.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()
    val = Validation(
        validation_id=uuid.uuid4(),
        plant_id=SEED_PLANT_ID,
        period_start=NOON_UTC.replace(tzinfo=None),
        period_end=(NOON_UTC + timedelta(hours=1)).replace(tzinfo=None),
        energy_kwh=12.3,
        confidence_score=100.0,
        status="approved",
        ntp_pass=True,
        ntp_drift_ms=0.5,
        physics_pass=True,
        theoretical_max_kwh=75.0,
        theoretical_max_kw=75.0,
        ghi_clear_sky_wm2=850.0,
        solar_elevation_deg=55.0,
        physics_method="analytical",
        satellite_pass=True,
        satellite_ghi_wm2=800.0,
        satellite_source="mock",
        satellite_max_kwh=72.0,
        cloud_cover_pct=10.0,
        consensus_pass=None,
        consensus_neighbors=0,
        sentinel_version="SENTINEL-AGIS-2.0",
    )
    db_session.add(val)
    db_session.commit()
    return plant, val


def _make_review_validation(db_session):
    """Create a REVIEW validation record."""
    val = Validation(
        validation_id=uuid.uuid4(),
        plant_id=SEED_PLANT_ID,
        period_start=NOON_UTC.replace(tzinfo=None),
        period_end=(NOON_UTC + timedelta(hours=1)).replace(tzinfo=None),
        energy_kwh=50.0,
        confidence_score=85.0,
        status="review",
        ntp_pass=True,
        ntp_drift_ms=0.5,
        physics_pass=True,
        theoretical_max_kwh=75.0,
        satellite_pass=False,
        satellite_ghi_wm2=200.0,
        sentinel_version="SENTINEL-AGIS-2.0",
    )
    db_session.add(val)
    db_session.commit()
    return val


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — build_certificate_json
# ═══════════════════════════════════════════════════════════════════

class TestBuildCertificateJSON:
    """Verifica estrutura do JSON canônico do certificado."""

    def test_has_all_sections(self, db_session):
        plant, val = _make_approved_validation(db_session)
        hec_id = uuid.uuid4()
        cert = build_certificate_json(hec_id, plant, val, ISSUED_AT)

        assert "certificate" in cert
        assert "plant" in cert
        assert "energy" in cert
        assert "validation" in cert
        assert "issuance" in cert

    def test_certificate_section(self, db_session):
        plant, val = _make_approved_validation(db_session)
        hec_id = uuid.uuid4()
        cert = build_certificate_json(hec_id, plant, val, ISSUED_AT)

        assert cert["certificate"]["hec_id"] == str(hec_id)
        assert cert["certificate"]["type"] == "HEC"
        assert cert["certificate"]["version"] == "1.0"
        assert cert["certificate"]["standard"] == "SENTINEL-AGIS-2.0"

    def test_plant_section(self, db_session):
        plant, val = _make_approved_validation(db_session)
        cert = build_certificate_json(uuid.uuid4(), plant, val, ISSUED_AT)

        assert cert["plant"]["plant_id"] == str(SEED_PLANT_ID)
        assert cert["plant"]["name"] == "Usina Teste ABSOLAR"
        assert cert["plant"]["lat"] == -23.55
        assert cert["plant"]["lng"] == -46.63
        assert cert["plant"]["capacity_kw"] == 75.0

    def test_energy_section(self, db_session):
        plant, val = _make_approved_validation(db_session)
        cert = build_certificate_json(uuid.uuid4(), plant, val, ISSUED_AT)

        assert cert["energy"]["energy_kwh"] == 12.3
        assert "period_start" in cert["energy"]
        assert "period_end" in cert["energy"]

    def test_validation_section_five_layers(self, db_session):
        plant, val = _make_approved_validation(db_session)
        cert = build_certificate_json(uuid.uuid4(), plant, val, ISSUED_AT)

        v = cert["validation"]
        assert v["confidence_score"] == 100.0
        assert v["status"] == "approved"
        assert v["ntp_pass"] is True
        assert v["physics_pass"] is True
        assert v["satellite_pass"] is True
        assert v["consensus_pass"] is None  # Inconclusive
        assert v["sentinel_version"] == "SENTINEL-AGIS-2.0"

    def test_issuance_section(self, db_session):
        plant, val = _make_approved_validation(db_session)
        cert = build_certificate_json(uuid.uuid4(), plant, val, ISSUED_AT)

        assert cert["issuance"]["status"] == "pending"
        assert cert["issuance"]["chain"] == "polygon"
        assert cert["issuance"]["issuer"] == "Solar One HUB / ABSOLAR"
        assert "issued_at" in cert["issuance"]


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — compute_certificate_hash
# ═══════════════════════════════════════════════════════════════════

class TestComputeHash:
    """Verifica SHA-256 determinístico do certificado."""

    def test_hash_is_64_hex_chars(self, db_session):
        plant, val = _make_approved_validation(db_session)
        cert = build_certificate_json(uuid.uuid4(), plant, val, ISSUED_AT)
        h = compute_certificate_hash(cert)

        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_is_deterministic(self, db_session):
        plant, val = _make_approved_validation(db_session)
        hec_id = uuid.uuid4()
        cert = build_certificate_json(hec_id, plant, val, ISSUED_AT)

        h1 = compute_certificate_hash(cert)
        h2 = compute_certificate_hash(cert)
        assert h1 == h2

    def test_different_json_different_hash(self, db_session):
        plant, val = _make_approved_validation(db_session)

        cert1 = build_certificate_json(uuid.uuid4(), plant, val, ISSUED_AT)
        cert2 = build_certificate_json(uuid.uuid4(), plant, val, ISSUED_AT)

        assert compute_certificate_hash(cert1) != compute_certificate_hash(cert2)

    def test_hash_matches_manual_sha256(self, db_session):
        plant, val = _make_approved_validation(db_session)
        hec_id = uuid.uuid4()
        cert = build_certificate_json(hec_id, plant, val, ISSUED_AT)

        canonical = json.dumps(cert, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        assert compute_certificate_hash(cert) == expected

    def test_sort_keys_ensures_determinism(self):
        """Mesmos dados em ordem diferente → mesmo hash."""
        d1 = {"b": 2, "a": 1, "c": 3}
        d2 = {"a": 1, "c": 3, "b": 2}

        h1 = compute_certificate_hash(d1)
        h2 = compute_certificate_hash(d2)
        assert h1 == h2


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — generate_certificate_pdf
# ═══════════════════════════════════════════════════════════════════

class TestGeneratePDF:
    """Verifica geração do PDF do certificado."""

    def test_pdf_is_bytes(self, db_session):
        plant, val = _make_approved_validation(db_session)
        cert = build_certificate_json(uuid.uuid4(), plant, val, ISSUED_AT)
        h = compute_certificate_hash(cert)
        pdf = generate_certificate_pdf(cert, h)

        assert isinstance(pdf, bytes)

    def test_pdf_starts_with_header(self, db_session):
        plant, val = _make_approved_validation(db_session)
        cert = build_certificate_json(uuid.uuid4(), plant, val, ISSUED_AT)
        h = compute_certificate_hash(cert)
        pdf = generate_certificate_pdf(cert, h)

        assert pdf[:5] == b"%PDF-"

    def test_pdf_reasonable_size(self, db_session):
        plant, val = _make_approved_validation(db_session)
        cert = build_certificate_json(uuid.uuid4(), plant, val, ISSUED_AT)
        h = compute_certificate_hash(cert)
        pdf = generate_certificate_pdf(cert, h)

        assert len(pdf) > 1024  # At least 1KB
        assert len(pdf) < 500_000  # Less than 500KB


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — issue_hec
# ═══════════════════════════════════════════════════════════════════

class TestIssueHEC:
    """Testes da função issue_hec."""

    def test_approved_emits_registered(self, db_session):
        plant, val = _make_approved_validation(db_session)
        result = issue_hec(db_session, plant, val, issued_at=ISSUED_AT)
        db_session.commit()

        assert isinstance(result, HECIssuanceResult)
        assert result.status == "registered"
        assert result.energy_kwh == 12.3
        assert len(result.certificate_hash) == 64
        assert isinstance(result.pdf_bytes, bytes)
        assert result.pdf_bytes[:5] == b"%PDF-"
        # IPFS CIDs
        assert result.ipfs_json_cid is not None
        assert result.ipfs_json_cid.startswith("Qm")
        assert result.ipfs_pdf_cid is not None
        assert result.ipfs_pdf_cid.startswith("Qm")
        assert result.ipfs_provider == "mock"
        # On-chain registry
        assert result.registry_tx_hash is not None
        assert result.registry_tx_hash.startswith("0x")
        assert result.registry_block is not None
        assert result.registry_block > 0
        assert result.contract_address is not None
        assert result.registered_at is not None

    def test_review_raises_value_error(self, db_session):
        val = _make_review_validation(db_session)
        plant = db_session.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()

        with pytest.raises(ValueError, match="APPROVED"):
            issue_hec(db_session, plant, val)

    def test_rejected_raises_value_error(self, db_session):
        val = Validation(
            validation_id=uuid.uuid4(),
            plant_id=SEED_PLANT_ID,
            period_start=NOON_UTC.replace(tzinfo=None),
            period_end=(NOON_UTC + timedelta(hours=1)).replace(tzinfo=None),
            energy_kwh=200.0,
            confidence_score=50.0,
            status="rejected",
            sentinel_version="SENTINEL-AGIS-2.0",
        )
        db_session.add(val)
        db_session.commit()
        plant = db_session.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()

        with pytest.raises(ValueError, match="APPROVED"):
            issue_hec(db_session, plant, val)

    def test_hec_record_created_in_db(self, db_session):
        plant, val = _make_approved_validation(db_session)
        result = issue_hec(db_session, plant, val, issued_at=ISSUED_AT)
        db_session.commit()

        hec = db_session.query(HECCertificate).filter(
            HECCertificate.hec_id == result.hec_id
        ).first()

        assert hec is not None
        assert hec.hash_sha256 == result.certificate_hash
        assert hec.status == "registered"
        assert float(hec.energy_kwh) == 12.3
        assert hec.certificate_json is not None
        assert hec.certificate_json["certificate"]["hec_id"] == str(result.hec_id)
        # IPFS columns persisted
        assert hec.ipfs_json_cid is not None
        assert hec.ipfs_json_cid.startswith("Qm")
        assert hec.ipfs_pdf_cid is not None
        assert hec.ipfs_provider == "mock"
        # On-chain columns persisted
        assert hec.registry_tx_hash is not None
        assert hec.registry_tx_hash.startswith("0x")
        assert hec.registry_block is not None
        assert hec.registry_block > 0
        assert hec.contract_address is not None
        assert hec.registered_at is not None

    def test_hash_unique_per_certificate(self, db_session):
        """Dois certificados diferentes têm hashes diferentes."""
        plant, val1 = _make_approved_validation(db_session)
        result1 = issue_hec(db_session, plant, val1, issued_at=ISSUED_AT)
        db_session.commit()

        # Create second validation
        val2 = Validation(
            validation_id=uuid.uuid4(),
            plant_id=SEED_PLANT_ID,
            period_start=(NOON_UTC + timedelta(hours=1)).replace(tzinfo=None),
            period_end=(NOON_UTC + timedelta(hours=2)).replace(tzinfo=None),
            energy_kwh=15.0,
            confidence_score=100.0,
            status="approved",
            ntp_pass=True,
            physics_pass=True,
            satellite_pass=True,
            sentinel_version="SENTINEL-AGIS-2.0",
        )
        db_session.add(val2)
        db_session.commit()

        result2 = issue_hec(db_session, plant, val2, issued_at=ISSUED_AT)
        db_session.commit()

        assert result1.certificate_hash != result2.certificate_hash


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — POST /hec/issue
# ═══════════════════════════════════════════════════════════════════

class TestHECEndpointIssue:
    """Testes do endpoint POST /hec/issue."""

    def test_issue_approved_201(self, client, db_session):
        _, val = _make_approved_validation(db_session)

        r = client.post("/hec/issue", json={"validation_id": str(val.validation_id)})
        assert r.status_code == 201
        data = r.json()

        assert data["status"] == "registered"
        assert data["energy_kwh"] == 12.3
        assert len(data["certificate_hash"]) == 64
        assert data["hec_id"] is not None
        assert data["certificate_json"] is not None
        assert data["pdf_available"] is True
        # IPFS
        assert data["ipfs_json_cid"] is not None
        assert data["ipfs_json_cid"].startswith("Qm")
        assert data["ipfs_pdf_cid"] is not None
        assert data["ipfs_provider"] == "mock"
        assert "IPFS" in data["message"]
        # On-chain
        assert data["registry_tx_hash"] is not None
        assert data["registry_tx_hash"].startswith("0x")
        assert data["registry_block"] is not None
        assert data["contract_address"] is not None
        assert data["backing_complete"] is True
        assert "REGISTERED" in data["message"]

    def test_issue_duplicate_409(self, client, db_session):
        _, val = _make_approved_validation(db_session)

        r1 = client.post("/hec/issue", json={"validation_id": str(val.validation_id)})
        assert r1.status_code == 201

        r2 = client.post("/hec/issue", json={"validation_id": str(val.validation_id)})
        assert r2.status_code == 409

    def test_issue_review_422(self, client, db_session):
        val = _make_review_validation(db_session)

        r = client.post("/hec/issue", json={"validation_id": str(val.validation_id)})
        assert r.status_code == 422
        assert "APPROVED" in r.json()["detail"]

    def test_issue_nonexistent_404(self, client, db_session):
        fake_id = str(uuid.uuid4())
        r = client.post("/hec/issue", json={"validation_id": fake_id})
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — GET /hec/{hec_id}
# ═══════════════════════════════════════════════════════════════════

class TestHECEndpointGet:
    """Testes do endpoint GET /hec/{hec_id}."""

    def test_get_existing_hec(self, client, db_session):
        _, val = _make_approved_validation(db_session)

        r1 = client.post("/hec/issue", json={"validation_id": str(val.validation_id)})
        hec_id = r1.json()["hec_id"]

        r2 = client.get(f"/hec/{hec_id}")
        assert r2.status_code == 200
        data = r2.json()
        assert data["hec_id"] == hec_id
        assert data["status"] == "registered"
        assert len(data["certificate_hash"]) == 64
        assert data["backing_complete"] is True
        assert data["registry_tx_hash"] is not None

    def test_get_nonexistent_404(self, client, db_session):
        r = client.get(f"/hec/{uuid.uuid4()}")
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — Auto-issue via POST /telemetry
# ═══════════════════════════════════════════════════════════════════

class TestAutoIssueViaTelemetry:
    """Telemetria APPROVED auto-emite HEC."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        set_server_now_fn(_fixed_clock(NOON_UTC))
        set_satellite_provider(_SAT_PROVIDER)
        yield
        reset_server_now_fn()
        reset_satellite_provider()

    def test_approved_telemetry_has_hec(self, client, ecdsa_keys):
        """Score 100 → APPROVED → auto-issue HEC."""
        private_pem, public_pem = ecdsa_keys
        payload = _make_payload(private_pem, public_pem, energy_kwh=12.3)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        data = r.json()

        assert data["status"] == "accepted"
        assert data["confidence_score"] == 100.0
        assert data["hec_id"] is not None
        assert data["certificate_hash"] is not None
        assert len(data["certificate_hash"]) == 64
        assert "HEC" in data["message"]
        assert "REGISTERED" in data["message"]
        assert data["registry_tx_hash"] is not None
        assert data["registry_tx_hash"].startswith("0x")
        assert data["backing_complete"] is True

    def test_rejected_telemetry_no_hec(self, client, ecdsa_keys):
        """Physics fail → REJECTED → no HEC."""
        private_pem, public_pem = ecdsa_keys
        payload = _make_payload(private_pem, public_pem, energy_kwh=200.0)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        data = r.json()

        assert data["status"] == "rejected"
        assert data["hec_id"] is None
        assert data["certificate_hash"] is None
        assert data["registry_tx_hash"] is None
        assert data["backing_complete"] is False

    def test_auto_issue_persists_in_db(self, client, db_session, ecdsa_keys):
        """Auto-issued HEC exists in hec_certificates table."""
        private_pem, public_pem = ecdsa_keys
        payload = _make_payload(private_pem, public_pem, energy_kwh=12.3)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        data = r.json()

        hec = db_session.query(HECCertificate).filter(
            HECCertificate.hec_id == data["hec_id"]
        ).first()

        assert hec is not None
        assert hec.status == "registered"
        assert hec.hash_sha256 == data["certificate_hash"]
        assert hec.certificate_json is not None
        assert float(hec.energy_kwh) == 12.3
        assert hec.registry_tx_hash is not None
        assert hec.registry_tx_hash.startswith("0x")

    def test_review_telemetry_no_hec(self, client, ecdsa_keys):
        """Satellite fail → REVIEW (85) → no HEC."""
        set_satellite_provider(
            MockSatelliteProvider(fixed_ghi_wm2=100.0, fixed_cloud_cover_pct=80.0)
        )
        private_pem, public_pem = ecdsa_keys
        # 30 kWh with GHI=100 → satellite max ~9 → fail → score 85 REVIEW
        payload = _make_payload(private_pem, public_pem, energy_kwh=30.0)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        data = r.json()

        assert data["hec_id"] is None

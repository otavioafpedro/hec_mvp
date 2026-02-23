"""
Testes automatizados — IPFS Upload + Verificação de Integridade

Cenários cobertos:

  UNIT TESTS — MockIPFSProvider:
    ✅ Upload retorna CID válido (Qm...)
    ✅ Download retorna mesmos bytes
    ✅ Pin funciona
    ✅ CID diferente para conteúdos diferentes
    ✅ CID determinístico (mesmo conteúdo → mesmo CID)
    ✅ Download CID inexistente → None
    ✅ Clear limpa store

  UNIT TESTS — upload_certificate_to_ipfs:
    ✅ Upload JSON + PDF gera dois CIDs
    ✅ CIDs são diferentes (JSON vs PDF)
    ✅ Provider name correto
    ✅ Sizes corretos

  UNIT TESTS — verify_certificate_from_ipfs:
    ✅ JSON íntegro → verified=True, match=True, hash 100%
    ✅ JSON adulterado → verified=False, match=False, TAMPERED
    ✅ CID não encontrado → verified=False, reason contém "não encontrado"
    ✅ Hash recalculado bate com compute_certificate_hash()
    ✅ Certificate JSON recuperado do IPFS

  UNIT TESTS — TamperedMockIPFSProvider:
    ✅ Download altera 1 byte → hash diverge
    ✅ Verificação detecta adulteração

  UNIT TESTS — MissingMockIPFSProvider:
    ✅ Download sempre None → verificação falha

  INTEGRATION TESTS — POST /hec/issue + GET /hec/verify/{hec_id}:
    ✅ Issue → verify → VERIFIED (100%)
    ✅ HEC sem CID → 422 no verify
    ✅ HEC inexistente → 404 no verify
    ✅ Verify response contém todos os campos
    ✅ Verify retorna certificate_json do IPFS

  INTEGRATION TESTS — Auto-issue via POST /telemetry → verify:
    ✅ Telemetria APPROVED → auto-issue → verify → 100%
    ✅ DB record tem ipfs_json_cid e ipfs_pdf_cid

  TAMPER DETECTION:
    ✅ Inject tampered provider → verify → TAMPERED
    ✅ Inject missing provider → verify → não encontrado
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone, timedelta

import pytest

from app.ipfs_service import (
    MockIPFSProvider,
    TamperedMockIPFSProvider,
    MissingMockIPFSProvider,
    upload_certificate_to_ipfs,
    verify_certificate_from_ipfs,
    set_ipfs_provider,
    reset_ipfs_provider,
    get_ipfs_provider,
    IPFSUploadResult,
    IPFSVerifyResult,
)
from app.hec_generator import (
    build_certificate_json,
    compute_certificate_hash,
    generate_certificate_pdf,
    issue_hec,
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


def _build_test_cert():
    """Build a test certificate JSON without DB."""
    return {
        "certificate": {"hec_id": str(uuid.uuid4()), "type": "HEC",
                         "version": "1.0", "standard": "SENTINEL-AGIS-2.0"},
        "plant": {"plant_id": str(uuid.uuid4()), "name": "Test Plant",
                   "absolar_id": "TEST", "lat": -23.55, "lng": -46.63,
                   "capacity_kw": 75.0},
        "energy": {"energy_kwh": 12.3, "period_start": "2026-02-23T14:30:00Z",
                    "period_end": "2026-02-23T15:30:00Z"},
        "validation": {"validation_id": str(uuid.uuid4()),
                         "confidence_score": 100.0, "status": "approved",
                         "ntp_pass": True, "ntp_drift_ms": 0.5,
                         "physics_pass": True, "theoretical_max_kwh": 75.0,
                         "satellite_pass": True, "satellite_ghi_wm2": 800.0,
                         "consensus_pass": None, "consensus_deviation_pct": None,
                         "consensus_neighbors": 0,
                         "sentinel_version": "SENTINEL-AGIS-2.0"},
        "issuance": {"issued_at": "2026-02-23T15:00:00+00:00Z",
                      "issuer": "Solar One HUB / ABSOLAR",
                      "chain": "polygon", "status": "pending"},
    }


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — MockIPFSProvider
# ═══════════════════════════════════════════════════════════════════

class TestMockIPFSProvider:
    """Testes do provider IPFS mock."""

    def test_upload_returns_cid(self):
        prov = MockIPFSProvider()
        cid = prov.upload(b"hello world", "test.txt")
        assert cid.startswith("Qm")
        assert len(cid) == 46  # Qm + 44 hex chars

    def test_download_returns_same_bytes(self):
        prov = MockIPFSProvider()
        data = b"test content 12345"
        cid = prov.upload(data, "test.txt")
        downloaded = prov.download(cid)
        assert downloaded == data

    def test_pin_returns_true(self):
        prov = MockIPFSProvider()
        cid = prov.upload(b"data", "f.txt")
        assert prov.pin(cid) is True

    def test_pin_nonexistent_returns_false(self):
        prov = MockIPFSProvider()
        assert prov.pin("QmNONEXISTENT") is False

    def test_different_content_different_cid(self):
        prov = MockIPFSProvider()
        cid1 = prov.upload(b"content A", "a.txt")
        cid2 = prov.upload(b"content B", "b.txt")
        assert cid1 != cid2

    def test_same_content_same_cid(self):
        """Content-addressed: same content → same CID."""
        prov = MockIPFSProvider()
        cid1 = prov.upload(b"identical", "file1.txt")
        cid2 = prov.upload(b"identical", "file2.txt")
        assert cid1 == cid2

    def test_download_nonexistent_returns_none(self):
        prov = MockIPFSProvider()
        assert prov.download("QmFAKECID") is None

    def test_clear_empties_store(self):
        prov = MockIPFSProvider()
        prov.upload(b"data", "f.txt")
        assert prov.store_size == 1
        prov.clear()
        assert prov.store_size == 0

    def test_provider_name(self):
        assert MockIPFSProvider().name == "mock"


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — TamperedMockIPFSProvider
# ═══════════════════════════════════════════════════════════════════

class TestTamperedProvider:
    """Provider que simula adulteração."""

    def test_download_alters_content(self):
        prov = TamperedMockIPFSProvider()
        original = b"0123456789ABCDEF0123456789"  # > 10 bytes
        cid = prov.upload(original, "test.txt")
        downloaded = prov.download(cid)
        assert downloaded != original
        assert len(downloaded) == len(original)
        # Only byte 10 should differ
        diff_count = sum(1 for a, b in zip(original, downloaded) if a != b)
        assert diff_count == 1

    def test_tampered_hash_diverges(self):
        prov = TamperedMockIPFSProvider()
        data = b"test certificate content here with enough bytes"
        cid = prov.upload(data, "cert.json")
        downloaded = prov.download(cid)
        assert hashlib.sha256(data).hexdigest() != hashlib.sha256(downloaded).hexdigest()


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — MissingMockIPFSProvider
# ═══════════════════════════════════════════════════════════════════

class TestMissingProvider:
    """Provider que simula CID não encontrado."""

    def test_download_always_none(self):
        prov = MissingMockIPFSProvider()
        cid = prov.upload(b"data", "test.txt")
        assert prov.download(cid) is None


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — upload_certificate_to_ipfs
# ═══════════════════════════════════════════════════════════════════

class TestUploadCertificate:
    """Testes do upload JSON + PDF para IPFS."""

    def test_upload_returns_two_cids(self):
        prov = MockIPFSProvider()
        cert = _build_test_cert()
        pdf = b"%PDF-fake-pdf-content"
        result = upload_certificate_to_ipfs(cert, pdf, "test-id", provider=prov)

        assert isinstance(result, IPFSUploadResult)
        assert result.json_cid.startswith("Qm")
        assert result.pdf_cid.startswith("Qm")
        assert result.json_cid != result.pdf_cid
        assert result.pinned is True

    def test_upload_sizes_correct(self):
        prov = MockIPFSProvider()
        cert = _build_test_cert()
        pdf = b"%PDF-1.4 fake content"
        result = upload_certificate_to_ipfs(cert, pdf, "test-id", provider=prov)

        # JSON size = canonical serialization
        canonical = json.dumps(cert, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=True).encode("utf-8")
        assert result.json_size_bytes == len(canonical)
        assert result.pdf_size_bytes == len(pdf)

    def test_upload_provider_name(self):
        prov = MockIPFSProvider()
        cert = _build_test_cert()
        result = upload_certificate_to_ipfs(cert, b"pdf", "id", provider=prov)
        assert result.provider == "mock"

    def test_upload_uses_singleton(self):
        """Without explicit provider, uses global singleton."""
        reset_ipfs_provider()
        cert = _build_test_cert()
        result = upload_certificate_to_ipfs(cert, b"pdf", "id")
        assert result.provider == "mock"
        assert result.json_cid.startswith("Qm")


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — verify_certificate_from_ipfs
# ═══════════════════════════════════════════════════════════════════

class TestVerifyCertificate:
    """Testes de verificação de integridade via IPFS."""

    def test_intact_json_verified(self):
        """JSON íntegro no IPFS → VERIFIED, hash match 100%."""
        prov = MockIPFSProvider()
        cert = _build_test_cert()
        cert_hash = compute_certificate_hash(cert)

        # Upload
        upload_result = upload_certificate_to_ipfs(cert, b"pdf", "id", provider=prov)

        # Verify
        result = verify_certificate_from_ipfs(
            hec_id="test-id",
            stored_hash=cert_hash,
            json_cid=upload_result.json_cid,
            pdf_cid=upload_result.pdf_cid,
            provider=prov,
        )

        assert result.verified is True
        assert result.match is True
        assert result.stored_hash == cert_hash
        assert result.recalculated_hash == cert_hash
        assert "VERIFIED" in result.reason
        assert result.json_size_bytes > 0

    def test_intact_json_returns_certificate(self):
        """Verificação retorna o JSON recuperado do IPFS."""
        prov = MockIPFSProvider()
        cert = _build_test_cert()
        cert_hash = compute_certificate_hash(cert)
        upload_result = upload_certificate_to_ipfs(cert, b"pdf", "id", provider=prov)

        result = verify_certificate_from_ipfs(
            hec_id="test-id", stored_hash=cert_hash,
            json_cid=upload_result.json_cid, provider=prov,
        )

        assert result.certificate_json is not None
        assert result.certificate_json["certificate"]["type"] == "HEC"

    def test_tampered_json_detected(self):
        """JSON adulterado → TAMPERED, hash diverge."""
        prov = TamperedMockIPFSProvider()
        cert = _build_test_cert()
        cert_hash = compute_certificate_hash(cert)
        upload_result = upload_certificate_to_ipfs(cert, b"pdf", "id", provider=prov)

        result = verify_certificate_from_ipfs(
            hec_id="test-id", stored_hash=cert_hash,
            json_cid=upload_result.json_cid, provider=prov,
        )

        assert result.verified is False
        assert result.match is False
        assert result.recalculated_hash != cert_hash
        assert "TAMPERED" in result.reason

    def test_missing_cid_not_verified(self):
        """CID não encontrado no IPFS → falha na verificação."""
        prov = MissingMockIPFSProvider()
        cert = _build_test_cert()
        cert_hash = compute_certificate_hash(cert)
        # Upload (stored in parent class, but download returns None)
        upload_result = upload_certificate_to_ipfs(cert, b"pdf", "id", provider=prov)

        result = verify_certificate_from_ipfs(
            hec_id="test-id", stored_hash=cert_hash,
            json_cid=upload_result.json_cid, provider=prov,
        )

        assert result.verified is False
        assert result.match is False
        assert "não encontrado" in result.reason

    def test_recalculated_hash_matches_compute(self):
        """Hash recalculado do IPFS == compute_certificate_hash()."""
        prov = MockIPFSProvider()
        cert = _build_test_cert()
        expected_hash = compute_certificate_hash(cert)
        upload_result = upload_certificate_to_ipfs(cert, b"pdf", "id", provider=prov)

        result = verify_certificate_from_ipfs(
            hec_id="test-id", stored_hash=expected_hash,
            json_cid=upload_result.json_cid, provider=prov,
        )

        assert result.recalculated_hash == expected_hash

    def test_wrong_stored_hash_detected(self):
        """Hash armazenado errado → match=False."""
        prov = MockIPFSProvider()
        cert = _build_test_cert()
        wrong_hash = "0" * 64
        upload_result = upload_certificate_to_ipfs(cert, b"pdf", "id", provider=prov)

        result = verify_certificate_from_ipfs(
            hec_id="test-id", stored_hash=wrong_hash,
            json_cid=upload_result.json_cid, provider=prov,
        )

        assert result.verified is False
        assert result.match is False
        assert result.stored_hash == wrong_hash
        assert result.recalculated_hash != wrong_hash


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — POST /hec/issue + GET /hec/verify/{hec_id}
# ═══════════════════════════════════════════════════════════════════

class TestVerifyEndpoint:
    """Testes do endpoint GET /hec/verify/{hec_id}."""

    def test_issue_then_verify_100_percent(self, client, db_session):
        """Issue → verify → VERIFIED, hash 100%."""
        _, val = _make_approved_validation(db_session)

        # Issue
        r1 = client.post("/hec/issue", json={"validation_id": str(val.validation_id)})
        assert r1.status_code == 201
        hec_id = r1.json()["hec_id"]

        # Verify
        r2 = client.get(f"/hec/verify/{hec_id}")
        assert r2.status_code == 200
        data = r2.json()

        assert data["verified"] is True
        assert data["match"] is True
        assert data["stored_hash"] == data["recalculated_hash"]
        assert len(data["stored_hash"]) == 64
        assert "VERIFIED" in data["reason"]
        assert data["json_cid"] is not None
        assert data["json_size_bytes"] > 0
        assert data["ipfs_provider"] == "mock"
        assert data["verified_at"] is not None

    def test_verify_returns_certificate_json(self, client, db_session):
        """Verify response contém certificate_json recuperado do IPFS."""
        _, val = _make_approved_validation(db_session)
        r1 = client.post("/hec/issue", json={"validation_id": str(val.validation_id)})
        hec_id = r1.json()["hec_id"]

        r2 = client.get(f"/hec/verify/{hec_id}")
        data = r2.json()

        assert data["certificate_json"] is not None
        assert data["certificate_json"]["certificate"]["type"] == "HEC"
        assert data["certificate_json"]["plant"]["name"] == "Usina Teste ABSOLAR"

    def test_verify_nonexistent_404(self, client, db_session):
        r = client.get(f"/hec/verify/{uuid.uuid4()}")
        assert r.status_code == 404

    def test_verify_no_ipfs_cid_422(self, client, db_session):
        """HEC sem CID IPFS → 422."""
        # Create HEC record without IPFS CID (manual insert)
        _, val = _make_approved_validation(db_session)
        hec_id = uuid.uuid4()
        hec = HECCertificate(
            hec_id=hec_id,
            validation_id=val.validation_id,
            hash_sha256="a" * 64,
            energy_kwh=12.3,
            status="pending",
        )
        db_session.add(hec)
        db_session.commit()

        r = client.get(f"/hec/verify/{hec_id}")
        assert r.status_code == 422
        assert "CID IPFS" in r.json()["detail"]

    def test_issue_response_has_ipfs_fields(self, client, db_session):
        """Issue response contém ipfs_json_cid, ipfs_pdf_cid, ipfs_provider."""
        _, val = _make_approved_validation(db_session)
        r = client.post("/hec/issue", json={"validation_id": str(val.validation_id)})
        data = r.json()

        assert data["ipfs_json_cid"] is not None
        assert data["ipfs_json_cid"].startswith("Qm")
        assert data["ipfs_pdf_cid"] is not None
        assert data["ipfs_provider"] == "mock"

    def test_get_hec_has_ipfs_fields(self, client, db_session):
        """GET /hec/{id} retorna IPFS fields."""
        _, val = _make_approved_validation(db_session)
        r1 = client.post("/hec/issue", json={"validation_id": str(val.validation_id)})
        hec_id = r1.json()["hec_id"]

        r2 = client.get(f"/hec/{hec_id}")
        data = r2.json()

        assert data["ipfs_json_cid"] is not None
        assert data["ipfs_pdf_cid"] is not None
        assert data["ipfs_provider"] == "mock"


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — Tamper detection via endpoint
# ═══════════════════════════════════════════════════════════════════

class TestTamperDetectionEndpoint:
    """Testes de detecção de adulteração via endpoint."""

    def test_tampered_ipfs_detected(self, client, db_session):
        """Inject tampered provider → verify → TAMPERED."""
        _, val = _make_approved_validation(db_session)

        # Issue with normal provider
        r1 = client.post("/hec/issue", json={"validation_id": str(val.validation_id)})
        assert r1.status_code == 201
        hec_id = r1.json()["hec_id"]

        # Swap to tampered provider (simulates IPFS data corruption)
        # We need to re-upload to tampered provider's store
        tampered_prov = TamperedMockIPFSProvider()
        # Copy the data from mock provider to tampered provider
        original_prov = get_ipfs_provider()
        hec_record = db_session.query(HECCertificate).filter(
            HECCertificate.hec_id == hec_id
        ).first()

        # Upload same data to tampered store
        cert_json = hec_record.certificate_json
        json_bytes = json.dumps(cert_json, sort_keys=True,
                                 separators=(",", ":"),
                                 ensure_ascii=True).encode("utf-8")
        cid = tampered_prov.upload(json_bytes, "cert.json")

        # Verify using tampered provider
        result = verify_certificate_from_ipfs(
            hec_id=str(hec_id),
            stored_hash=hec_record.hash_sha256,
            json_cid=cid,
            provider=tampered_prov,
        )

        assert result.verified is False
        assert result.match is False
        assert "TAMPERED" in result.reason


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — Auto-issue via POST /telemetry → verify
# ═══════════════════════════════════════════════════════════════════

class TestAutoIssueIPFS:
    """Telemetria APPROVED auto-emite HEC com IPFS → verify 100%."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        set_server_now_fn(_fixed_clock(NOON_UTC))
        set_satellite_provider(_SAT_PROVIDER)
        reset_ipfs_provider()
        yield
        reset_server_now_fn()
        reset_satellite_provider()
        reset_ipfs_provider()

    def test_telemetry_approved_verify_100(self, client, ecdsa_keys):
        """Full flow: telemetry → APPROVED → auto-HEC → IPFS → verify 100%."""
        private_pem, public_pem = ecdsa_keys
        payload = _make_payload(private_pem, public_pem, energy_kwh=12.3)

        # 1. Submit telemetry
        r1 = client.post("/telemetry", json=payload)
        assert r1.status_code == 201
        tel_data = r1.json()
        assert tel_data["status"] == "accepted"
        assert tel_data["hec_id"] is not None

        hec_id = tel_data["hec_id"]

        # 2. Verify via IPFS
        r2 = client.get(f"/hec/verify/{hec_id}")
        assert r2.status_code == 200
        ver_data = r2.json()

        assert ver_data["verified"] is True
        assert ver_data["match"] is True
        assert ver_data["stored_hash"] == ver_data["recalculated_hash"]
        assert "VERIFIED" in ver_data["reason"]

    def test_auto_issue_db_has_ipfs_cids(self, client, db_session, ecdsa_keys):
        """Auto-issued HEC persists IPFS CIDs in DB."""
        private_pem, public_pem = ecdsa_keys
        payload = _make_payload(private_pem, public_pem, energy_kwh=12.3)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        hec_id = r.json()["hec_id"]

        hec = db_session.query(HECCertificate).filter(
            HECCertificate.hec_id == hec_id
        ).first()

        assert hec is not None
        assert hec.ipfs_json_cid is not None
        assert hec.ipfs_json_cid.startswith("Qm")
        assert hec.ipfs_pdf_cid is not None
        assert hec.ipfs_pdf_cid.startswith("Qm")
        assert hec.ipfs_provider == "mock"

    def test_verify_returns_pdf_cid(self, client, ecdsa_keys):
        """Verify response includes PDF CID from DB."""
        private_pem, public_pem = ecdsa_keys
        payload = _make_payload(private_pem, public_pem, energy_kwh=12.3)

        r1 = client.post("/telemetry", json=payload)
        hec_id = r1.json()["hec_id"]

        r2 = client.get(f"/hec/verify/{hec_id}")
        data = r2.json()

        assert data["pdf_cid"] is not None
        assert data["pdf_cid"].startswith("Qm")

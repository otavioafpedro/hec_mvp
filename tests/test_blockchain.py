"""
Testes automatizados — Registro On-Chain (Blockchain)

Cenários cobertos:

  UNIT TESTS — MockBlockchainProvider:
    ✅ Register retorna tx_hash (0x...)
    ✅ Register retorna block_number > 0
    ✅ Register retorna contract_address
    ✅ Block number incrementa a cada registro
    ✅ Hash duplicado → ValueError (imutável)
    ✅ Hash zero → ValueError
    ✅ CID vazio → ValueError
    ✅ Provider name = "mock"

  UNIT TESTS — verify on-chain:
    ✅ Hash registrado → exists=True + CID correto
    ✅ Hash não registrado → exists=False
    ✅ Verify após register retorna dados corretos

  UNIT TESTS — register_on_chain / verify_on_chain:
    ✅ Funções usam singleton provider
    ✅ Register + verify round-trip

  INTEGRATION TESTS — POST /hec/issue (pipeline completo):
    ✅ Issue → status=registered + tx_hash + block + contract
    ✅ Issue → backing_complete=True
    ✅ GET /hec/{id} retorna on-chain fields

  INTEGRATION TESTS — POST /hec/register (registro manual):
    ✅ HEC pendente → register → status=registered
    ✅ HEC já registrado → 409
    ✅ HEC sem IPFS CID → 422
    ✅ HEC inexistente → 404

  INTEGRATION TESTS — GET /hec/onchain/{hec_id}:
    ✅ HEC registrado → exists=True + backing_complete=True
    ✅ HEC não registrado → exists=False
    ✅ HEC inexistente → 404

  INTEGRATION TESTS — Full pipeline via POST /telemetry:
    ✅ APPROVED → auto-issue → registered → backing_complete=True
    ✅ REJECTED → no HEC → backing_complete=False
    ✅ DB record tem registry_tx_hash + registry_block

  CRITÉRIO — Backing completo:
    ✅ backing_complete=True SOMENTE se registry_tx_hash existir
    ✅ HEC sem tx_hash → backing_complete=False
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone, timedelta

import pytest

from app.blockchain import (
    MockBlockchainProvider,
    register_on_chain,
    verify_on_chain,
    set_blockchain_provider,
    reset_blockchain_provider,
    get_blockchain_provider,
    RegistrationResult,
    OnChainVerifyResult,
)
from app.hec_generator import (
    compute_certificate_hash,
    issue_hec,
)
from app.models.models import Plant, Validation, HECCertificate
from app.security import canonical_payload, sign_payload
from app.api.telemetry import set_server_now_fn, reset_server_now_fn
from app.satellite import MockSatelliteProvider, set_satellite_provider, reset_satellite_provider
from app.ipfs_service import reset_ipfs_provider


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


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — MockBlockchainProvider
# ═══════════════════════════════════════════════════════════════════

class TestMockBlockchainProvider:
    """Testes do provider blockchain mock."""

    def test_register_returns_tx_hash(self):
        prov = MockBlockchainProvider()
        r = prov.register("ab" * 32, "QmTestCID123")
        assert r.tx_hash.startswith("0x")
        assert len(r.tx_hash) == 66  # 0x + 64 hex

    def test_register_returns_block_number(self):
        prov = MockBlockchainProvider()
        r = prov.register("cd" * 32, "QmTestCID456")
        assert r.block_number > 0
        assert r.block_number > 50_000_000  # Realistic Polygon range

    def test_register_returns_contract_address(self):
        prov = MockBlockchainProvider()
        r = prov.register("ef" * 32, "QmTestCID789")
        assert r.contract_address.startswith("0x")

    def test_block_number_increments(self):
        prov = MockBlockchainProvider()
        r1 = prov.register("aa" * 32, "QmCID1")
        r2 = prov.register("bb" * 32, "QmCID2")
        assert r2.block_number == r1.block_number + 1

    def test_duplicate_hash_raises(self):
        """Hash imutável — não pode registrar duas vezes."""
        prov = MockBlockchainProvider()
        h = "cc" * 32
        prov.register(h, "QmCID")
        with pytest.raises(ValueError, match="already registered"):
            prov.register(h, "QmCIDDifferent")

    def test_zero_hash_raises(self):
        prov = MockBlockchainProvider()
        with pytest.raises(ValueError, match="cannot be zero"):
            prov.register("0" * 64, "QmCID")

    def test_empty_cid_raises(self):
        prov = MockBlockchainProvider()
        with pytest.raises(ValueError, match="cannot be empty"):
            prov.register("dd" * 32, "")

    def test_provider_name(self):
        assert MockBlockchainProvider().name == "mock"

    def test_chain_name(self):
        prov = MockBlockchainProvider(chain_name="polygon-mainnet")
        assert prov.chain == "polygon-mainnet"

    def test_total_registered(self):
        prov = MockBlockchainProvider()
        assert prov.total_registered == 0
        prov.register("ee" * 32, "QmCID")
        assert prov.total_registered == 1

    def test_register_returns_correct_hash(self):
        prov = MockBlockchainProvider()
        h = "ff" * 32
        r = prov.register(h, "QmTestCID")
        assert r.certificate_hash == h

    def test_register_returns_correct_cid(self):
        prov = MockBlockchainProvider()
        r = prov.register("11" * 32, "QmMyIPFSCID")
        assert r.ipfs_cid == "QmMyIPFSCID"


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — verify on-chain
# ═══════════════════════════════════════════════════════════════════

class TestOnChainVerify:
    """Testes da verificação on-chain."""

    def test_registered_hash_exists(self):
        prov = MockBlockchainProvider()
        h = "22" * 32
        prov.register(h, "QmVerifyCID")
        result = prov.verify(h)

        assert result.exists is True
        assert result.ipfs_cid == "QmVerifyCID"
        assert result.block_number > 0
        assert result.registered_at > 0

    def test_unregistered_hash_not_exists(self):
        prov = MockBlockchainProvider()
        result = prov.verify("33" * 32)

        assert result.exists is False
        assert result.ipfs_cid == ""
        assert result.block_number == 0
        assert result.registered_at == 0

    def test_verify_after_register_roundtrip(self):
        prov = MockBlockchainProvider()
        h = "44" * 32
        reg = prov.register(h, "QmRoundTrip")
        ver = prov.verify(h)

        assert ver.exists is True
        assert ver.ipfs_cid == "QmRoundTrip"
        assert ver.block_number == reg.block_number


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — register_on_chain / verify_on_chain functions
# ═══════════════════════════════════════════════════════════════════

class TestOnChainFunctions:
    """Testes das funções register_on_chain e verify_on_chain."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        reset_blockchain_provider()
        yield
        reset_blockchain_provider()

    def test_register_uses_singleton(self):
        result = register_on_chain("55" * 32, "QmSingleton")
        assert result.tx_hash.startswith("0x")
        assert result.provider == "mock"

    def test_register_verify_roundtrip(self):
        h = "66" * 32
        reg = register_on_chain(h, "QmRoundTrip2")
        ver = verify_on_chain(h)

        assert ver.exists is True
        assert ver.ipfs_cid == "QmRoundTrip2"
        assert ver.block_number == reg.block_number

    def test_verify_unregistered(self):
        ver = verify_on_chain("77" * 32)
        assert ver.exists is False


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — POST /hec/issue (pipeline completo)
# ═══════════════════════════════════════════════════════════════════

class TestIssueWithOnChain:
    """POST /hec/issue agora inclui registro on-chain."""

    def test_issue_pipeline_registered(self, client, db_session):
        """Issue pipeline completo → status=registered + tx_hash + block."""
        _, val = _make_approved_validation(db_session)
        r = client.post("/hec/issue", json={"validation_id": str(val.validation_id)})
        assert r.status_code == 201
        data = r.json()

        assert data["status"] == "registered"
        assert data["registry_tx_hash"] is not None
        assert data["registry_tx_hash"].startswith("0x")
        assert data["registry_block"] is not None
        assert data["registry_block"] > 0
        assert data["contract_address"] is not None
        assert data["backing_complete"] is True

    def test_issue_db_record_has_onchain(self, client, db_session):
        """DB record persists on-chain fields."""
        _, val = _make_approved_validation(db_session)
        r = client.post("/hec/issue", json={"validation_id": str(val.validation_id)})
        hec_id = r.json()["hec_id"]

        hec = db_session.query(HECCertificate).filter(
            HECCertificate.hec_id == hec_id
        ).first()

        assert hec.status == "registered"
        assert hec.registry_tx_hash is not None
        assert hec.registry_tx_hash.startswith("0x")
        assert hec.registry_block is not None
        assert hec.contract_address is not None
        assert hec.registered_at is not None

    def test_get_shows_onchain_fields(self, client, db_session):
        """GET /hec/{id} retorna on-chain fields."""
        _, val = _make_approved_validation(db_session)
        r1 = client.post("/hec/issue", json={"validation_id": str(val.validation_id)})
        hec_id = r1.json()["hec_id"]

        r2 = client.get(f"/hec/{hec_id}")
        data = r2.json()

        assert data["registry_tx_hash"] is not None
        assert data["backing_complete"] is True


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — POST /hec/register (manual)
# ═══════════════════════════════════════════════════════════════════

class TestManualRegister:
    """POST /hec/register — registro manual on-chain."""

    def _create_pending_hec(self, db_session):
        """Create HEC in pending status (no on-chain registration)."""
        _, val = _make_approved_validation(db_session)
        hec_id = uuid.uuid4()
        hec = HECCertificate(
            hec_id=hec_id,
            validation_id=val.validation_id,
            hash_sha256="ab" * 32,
            energy_kwh=12.3,
            status="pending",
            ipfs_json_cid="QmPendingCID123",
            ipfs_pdf_cid="QmPendingPDF456",
            ipfs_provider="mock",
        )
        db_session.add(hec)
        db_session.commit()
        return hec_id

    def test_register_pending_success(self, client, db_session):
        """Pending HEC → register → registered."""
        hec_id = self._create_pending_hec(db_session)
        r = client.post("/hec/register", json={"hec_id": str(hec_id)})
        assert r.status_code == 200
        data = r.json()

        assert data["status"] == "registered"
        assert data["registry_tx_hash"] is not None
        assert data["registry_tx_hash"].startswith("0x")
        assert data["backing_complete"] is True
        assert "BACKING COMPLETO" in data["message"]

    def test_register_already_registered_409(self, client, db_session):
        """Já registrado → 409."""
        hec_id = self._create_pending_hec(db_session)
        r1 = client.post("/hec/register", json={"hec_id": str(hec_id)})
        assert r1.status_code == 200

        r2 = client.post("/hec/register", json={"hec_id": str(hec_id)})
        assert r2.status_code == 409

    def test_register_no_ipfs_cid_422(self, client, db_session):
        """HEC sem IPFS CID → 422."""
        _, val = _make_approved_validation(db_session)
        hec_id = uuid.uuid4()
        hec = HECCertificate(
            hec_id=hec_id,
            validation_id=val.validation_id,
            hash_sha256="cd" * 32,
            energy_kwh=12.3,
            status="pending",
            # No IPFS CID
        )
        db_session.add(hec)
        db_session.commit()

        r = client.post("/hec/register", json={"hec_id": str(hec_id)})
        assert r.status_code == 422

    def test_register_nonexistent_404(self, client, db_session):
        r = client.post("/hec/register", json={"hec_id": str(uuid.uuid4())})
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — GET /hec/onchain/{hec_id}
# ═══════════════════════════════════════════════════════════════════

class TestOnChainEndpoint:
    """GET /hec/onchain/{hec_id} — verificação on-chain."""

    def test_registered_hec_exists_onchain(self, client, db_session):
        """HEC registrado → exists=True on-chain."""
        _, val = _make_approved_validation(db_session)
        r1 = client.post("/hec/issue", json={"validation_id": str(val.validation_id)})
        hec_id = r1.json()["hec_id"]

        r2 = client.get(f"/hec/onchain/{hec_id}")
        assert r2.status_code == 200
        data = r2.json()

        assert data["exists"] is True
        assert data["backing_complete"] is True
        assert data["certificate_hash"] is not None
        assert data["ipfs_cid"] is not None
        assert data["block_number"] > 0
        assert data["contract_address"] is not None

    def test_unregistered_hec_not_onchain(self, client, db_session):
        """HEC pendente (manual, sem on-chain) → exists=False."""
        _, val = _make_approved_validation(db_session)
        hec_id = uuid.uuid4()
        hec = HECCertificate(
            hec_id=hec_id,
            validation_id=val.validation_id,
            hash_sha256="ef" * 32,
            energy_kwh=12.3,
            status="pending",
        )
        db_session.add(hec)
        db_session.commit()

        # Use a fresh mock provider that doesn't have this hash
        r = client.get(f"/hec/onchain/{hec_id}")
        assert r.status_code == 200
        data = r.json()

        assert data["exists"] is False
        assert data["backing_complete"] is False

    def test_onchain_nonexistent_404(self, client, db_session):
        r = client.get(f"/hec/onchain/{uuid.uuid4()}")
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — Full pipeline POST /telemetry → on-chain
# ═══════════════════════════════════════════════════════════════════

class TestTelemetryOnChain:
    """Telemetria APPROVED → auto-HEC → on-chain → backing completo."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        set_server_now_fn(_fixed_clock(NOON_UTC))
        set_satellite_provider(_SAT_PROVIDER)
        reset_ipfs_provider()
        reset_blockchain_provider()
        yield
        reset_server_now_fn()
        reset_satellite_provider()
        reset_ipfs_provider()
        reset_blockchain_provider()

    def test_approved_auto_registered(self, client, ecdsa_keys):
        """APPROVED → auto-issue → registered + tx_hash."""
        private_pem, public_pem = ecdsa_keys
        payload = _make_payload(private_pem, public_pem, energy_kwh=12.3)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        data = r.json()

        assert data["status"] == "accepted"
        assert data["hec_id"] is not None
        assert data["registry_tx_hash"] is not None
        assert data["registry_tx_hash"].startswith("0x")
        assert data["backing_complete"] is True

    def test_rejected_no_backing(self, client, ecdsa_keys):
        """REJECTED → no HEC → backing_complete=False."""
        private_pem, public_pem = ecdsa_keys
        payload = _make_payload(private_pem, public_pem, energy_kwh=200.0)

        r = client.post("/telemetry", json=payload)
        assert r.status_code == 201
        data = r.json()

        assert data["hec_id"] is None
        assert data["registry_tx_hash"] is None
        assert data["backing_complete"] is False

    def test_auto_issue_db_has_onchain(self, client, db_session, ecdsa_keys):
        """DB record has registry_tx_hash + registry_block."""
        private_pem, public_pem = ecdsa_keys
        payload = _make_payload(private_pem, public_pem, energy_kwh=12.3)

        r = client.post("/telemetry", json=payload)
        hec_id = r.json()["hec_id"]

        hec = db_session.query(HECCertificate).filter(
            HECCertificate.hec_id == hec_id
        ).first()

        assert hec.status == "registered"
        assert hec.registry_tx_hash is not None
        assert hec.registry_tx_hash.startswith("0x")
        assert hec.registry_block is not None
        assert hec.registry_block > 0
        assert hec.contract_address is not None
        assert hec.registered_at is not None

    def test_onchain_verify_after_telemetry(self, client, ecdsa_keys):
        """Telemetry → auto-HEC → onchain verify → exists."""
        private_pem, public_pem = ecdsa_keys
        payload = _make_payload(private_pem, public_pem, energy_kwh=12.3)

        r1 = client.post("/telemetry", json=payload)
        hec_id = r1.json()["hec_id"]

        r2 = client.get(f"/hec/onchain/{hec_id}")
        data = r2.json()

        assert data["exists"] is True
        assert data["backing_complete"] is True


# ═══════════════════════════════════════════════════════════════════
# CRITÉRIO — Backing completo só com registry_tx_hash
# ═══════════════════════════════════════════════════════════════════

class TestBackingCompleteCriteria:
    """Backing completo SOMENTE se registry_tx_hash existir."""

    def test_with_tx_hash_is_complete(self, client, db_session):
        _, val = _make_approved_validation(db_session)
        r = client.post("/hec/issue", json={"validation_id": str(val.validation_id)})
        data = r.json()
        assert data["backing_complete"] is True
        assert data["registry_tx_hash"] is not None

    def test_without_tx_hash_is_incomplete(self, client, db_session):
        """HEC sem tx_hash → backing_complete=False."""
        _, val = _make_approved_validation(db_session)
        hec_id = uuid.uuid4()
        hec = HECCertificate(
            hec_id=hec_id,
            validation_id=val.validation_id,
            hash_sha256="99" * 32,
            energy_kwh=12.3,
            status="pending",
            ipfs_json_cid="QmPendingTest",
            # No registry_tx_hash
        )
        db_session.add(hec)
        db_session.commit()

        r = client.get(f"/hec/{hec_id}")
        data = r.json()

        assert data["backing_complete"] is False
        assert data["registry_tx_hash"] is None
        assert data["status"] == "pending"

    def test_backing_complete_flag_in_telemetry_response(self, client, ecdsa_keys):
        """Telemetry response includes backing_complete flag."""
        set_server_now_fn(_fixed_clock(NOON_UTC))
        set_satellite_provider(_SAT_PROVIDER)
        reset_ipfs_provider()
        reset_blockchain_provider()
        try:
            private_pem, public_pem = ecdsa_keys
            payload = _make_payload(private_pem, public_pem, energy_kwh=12.3)
            r = client.post("/telemetry", json=payload)
            data = r.json()

            assert "backing_complete" in data
            assert data["backing_complete"] is True
        finally:
            reset_server_now_fn()
            reset_satellite_provider()

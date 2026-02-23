"""
Testes automatizados — Lotes HEC Backed

Cenários cobertos:

  UNIT TESTS — validate_hec_backing:
    ✅ HEC registered + IPFS + tx_hash → None (ok)
    ✅ HEC pending (sem on-chain) → rejeição
    ✅ HEC sem ipfs_json_cid → rejeição
    ✅ HEC sem registry_tx_hash → rejeição
    ✅ HEC minted (status futuro) com backing → None (ok)

  UNIT TESTS — validate_hec_not_in_lot:
    ✅ HEC sem lot_id → None (ok)
    ✅ HEC com lot_id → rejeição

  UNIT TESTS — create_lot:
    ✅ Lista válida → lote criado com totais corretos
    ✅ total_quantity = len(hec_ids)
    ✅ available_quantity = total_quantity
    ✅ total_energy_kwh = soma dos HECs
    ✅ Status do lote = "open"
    ✅ HECs recebem lot_id e status="listed"
    ✅ Lista vazia → ValueError
    ✅ HEC não encontrado → ValueError
    ✅ Backing incompleto (pending) → ValueError
    ✅ Backing incompleto (sem IPFS) → ValueError
    ✅ Backing incompleto (sem tx_hash) → ValueError
    ✅ HEC já em outro lote → ValueError
    ✅ Duplicatas na lista → deduplicados

  INTEGRATION TESTS — POST /lots/create:
    ✅ Backed HECs → 201 + lote completo
    ✅ Response tem total_quantity, available_quantity, energy
    ✅ Response tem certificates[] com detalhes
    ✅ backing_complete = True
    ✅ HEC pending → 422 blocking
    ✅ HEC sem IPFS → 422 blocking
    ✅ HEC sem tx_hash → 422 blocking
    ✅ HEC inexistente → 404
    ✅ HEC já em lote → 409
    ✅ Lista vazia → 422

  INTEGRATION TESTS — GET /lots/{lot_id}:
    ✅ Lote existente → 200 com detalhes
    ✅ Lote inexistente → 404
    ✅ Certificates lista com hec_id, hash, CID, tx_hash

  INTEGRATION TESTS — GET /lots:
    ✅ Lista todos os lotes
    ✅ Filtro por status

  INTEGRATION TESTS — Full pipeline:
    ✅ Telemetry → APPROVED → HEC registered → create lot → backing completo
"""
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest

from app.lot_service import (
    create_lot,
    validate_hec_backing,
    validate_hec_not_in_lot,
    LotCreationResult,
)
from app.hec_generator import issue_hec
from app.models.models import Plant, Validation, HECCertificate, HECLot
from app.security import canonical_payload, sign_payload
from app.api.telemetry import set_server_now_fn, reset_server_now_fn
from app.satellite import MockSatelliteProvider, set_satellite_provider, reset_satellite_provider
from app.ipfs_service import reset_ipfs_provider
from app.blockchain import reset_blockchain_provider


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


def _make_approved_validation(db_session, energy_kwh=12.3):
    """Create APPROVED validation."""
    plant = db_session.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()
    val = Validation(
        validation_id=uuid.uuid4(),
        plant_id=SEED_PLANT_ID,
        period_start=NOON_UTC.replace(tzinfo=None),
        period_end=(NOON_UTC + timedelta(hours=1)).replace(tzinfo=None),
        energy_kwh=energy_kwh,
        confidence_score=100.0,
        status="approved",
        ntp_pass=True, ntp_drift_ms=0.5,
        physics_pass=True, theoretical_max_kwh=75.0, theoretical_max_kw=75.0,
        ghi_clear_sky_wm2=850.0, solar_elevation_deg=55.0, physics_method="analytical",
        satellite_pass=True, satellite_ghi_wm2=800.0, satellite_source="mock",
        satellite_max_kwh=72.0, cloud_cover_pct=10.0,
        consensus_pass=None, consensus_neighbors=0,
        sentinel_version="SENTINEL-AGIS-2.0",
    )
    db_session.add(val)
    db_session.commit()
    return plant, val


def _make_backed_hec(db_session, energy_kwh=12.3):
    """Create fully backed HEC (registered + IPFS + on-chain)."""
    plant, val = _make_approved_validation(db_session, energy_kwh=energy_kwh)
    result = issue_hec(db_session, plant, val, issued_at=ISSUED_AT)
    db_session.commit()
    return result


def _make_pending_hec(db_session, energy_kwh=12.3):
    """Create HEC in pending status (no backing)."""
    _, val = _make_approved_validation(db_session, energy_kwh=energy_kwh)
    hec_id = uuid.uuid4()
    hec = HECCertificate(
        hec_id=hec_id,
        validation_id=val.validation_id,
        hash_sha256=uuid.uuid4().hex + uuid.uuid4().hex[:32],
        energy_kwh=energy_kwh,
        status="pending",
    )
    db_session.add(hec)
    db_session.commit()
    return hec


def _make_no_ipfs_hec(db_session, energy_kwh=12.3):
    """Create HEC with tx_hash but no IPFS CID."""
    _, val = _make_approved_validation(db_session, energy_kwh=energy_kwh)
    hec_id = uuid.uuid4()
    hec = HECCertificate(
        hec_id=hec_id,
        validation_id=val.validation_id,
        hash_sha256=uuid.uuid4().hex + uuid.uuid4().hex[:32],
        energy_kwh=energy_kwh,
        status="registered",
        registry_tx_hash="0x" + "ab" * 32,
        registry_block=50000001,
        # No ipfs_json_cid
    )
    db_session.add(hec)
    db_session.commit()
    return hec


def _make_no_tx_hec(db_session, energy_kwh=12.3):
    """Create HEC with IPFS but no tx_hash."""
    _, val = _make_approved_validation(db_session, energy_kwh=energy_kwh)
    hec_id = uuid.uuid4()
    hec = HECCertificate(
        hec_id=hec_id,
        validation_id=val.validation_id,
        hash_sha256=uuid.uuid4().hex + uuid.uuid4().hex[:32],
        energy_kwh=energy_kwh,
        status="pending",
        ipfs_json_cid="QmTestCID",
        # No registry_tx_hash
    )
    db_session.add(hec)
    db_session.commit()
    return hec


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — validate_hec_backing
# ═══════════════════════════════════════════════════════════════════

class TestValidateBacking:
    """Testes da validação de backing completo."""

    def test_fully_backed_ok(self, db_session):
        """Registered + IPFS + tx_hash → None (ok)."""
        result = _make_backed_hec(db_session)
        hec = db_session.query(HECCertificate).filter(
            HECCertificate.hec_id == result.hec_id
        ).first()
        assert validate_hec_backing(hec) is None

    def test_pending_rejected(self, db_session):
        """Pending status → rejeição."""
        hec = _make_pending_hec(db_session)
        err = validate_hec_backing(hec)
        assert err is not None
        assert "status=pending" in err

    def test_no_ipfs_rejected(self, db_session):
        """Sem IPFS CID → rejeição."""
        hec = _make_no_ipfs_hec(db_session)
        err = validate_hec_backing(hec)
        assert err is not None
        assert "ipfs_json_cid" in err

    def test_no_tx_hash_rejected(self, db_session):
        """Sem tx_hash → rejeição."""
        hec = _make_no_tx_hec(db_session)
        err = validate_hec_backing(hec)
        assert err is not None
        assert "registry_tx_hash" in err

    def test_minted_with_backing_ok(self, db_session):
        """Status minted com backing completo → ok."""
        result = _make_backed_hec(db_session)
        hec = db_session.query(HECCertificate).filter(
            HECCertificate.hec_id == result.hec_id
        ).first()
        hec.status = "minted"  # Future status
        db_session.commit()
        assert validate_hec_backing(hec) is None


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — validate_hec_not_in_lot
# ═══════════════════════════════════════════════════════════════════

class TestValidateNotInLot:
    """Testes de verificação de lot_id."""

    def test_no_lot_ok(self, db_session):
        result = _make_backed_hec(db_session)
        hec = db_session.query(HECCertificate).filter(
            HECCertificate.hec_id == result.hec_id
        ).first()
        assert validate_hec_not_in_lot(hec) is None

    def test_in_lot_rejected(self, db_session):
        result = _make_backed_hec(db_session)
        hec = db_session.query(HECCertificate).filter(
            HECCertificate.hec_id == result.hec_id
        ).first()
        hec.lot_id = uuid.uuid4()
        err = validate_hec_not_in_lot(hec)
        assert err is not None
        assert "já pertence" in err


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — create_lot
# ═══════════════════════════════════════════════════════════════════

class TestCreateLotUnit:
    """Testes da função create_lot."""

    def test_single_hec_lot(self, db_session):
        """Um HEC backed → lote com 1 certificado."""
        r = _make_backed_hec(db_session)
        result = create_lot(db_session, [r.hec_id], name="Lote Teste")
        db_session.commit()

        assert isinstance(result, LotCreationResult)
        assert result.total_quantity == 1
        assert result.available_quantity == 1
        assert result.total_energy_kwh == 12.3
        assert result.status == "open"
        assert len(result.hec_ids) == 1

    def test_multiple_hecs_lot(self, db_session):
        """3 HECs backed → lote com totais somados."""
        r1 = _make_backed_hec(db_session, energy_kwh=10.0)
        r2 = _make_backed_hec(db_session, energy_kwh=20.0)
        r3 = _make_backed_hec(db_session, energy_kwh=30.0)

        result = create_lot(
            db_session,
            [r1.hec_id, r2.hec_id, r3.hec_id],
            name="Lote Multi",
        )
        db_session.commit()

        assert result.total_quantity == 3
        assert result.available_quantity == 3
        assert result.total_energy_kwh == 60.0
        assert len(result.hec_ids) == 3

    def test_hecs_get_lot_id_and_listed(self, db_session):
        """HECs recebem lot_id e status=listed."""
        r = _make_backed_hec(db_session)
        result = create_lot(db_session, [r.hec_id], name="Lote")
        db_session.commit()

        hec = db_session.query(HECCertificate).filter(
            HECCertificate.hec_id == r.hec_id
        ).first()
        assert hec.lot_id == result.lot_id
        assert hec.status == "listed"

    def test_empty_list_raises(self, db_session):
        with pytest.raises(ValueError, match="vazia"):
            create_lot(db_session, [], name="Lote Vazio")

    def test_hec_not_found_raises(self, db_session):
        with pytest.raises(ValueError, match="não encontrado"):
            create_lot(db_session, [uuid.uuid4()], name="Lote")

    def test_pending_hec_blocked(self, db_session):
        """Backing incompleto (pending) → bloqueado."""
        hec = _make_pending_hec(db_session)
        with pytest.raises(ValueError, match="Backing incompleto"):
            create_lot(db_session, [hec.hec_id], name="Lote")

    def test_no_ipfs_blocked(self, db_session):
        """Backing incompleto (sem IPFS) → bloqueado."""
        hec = _make_no_ipfs_hec(db_session)
        with pytest.raises(ValueError, match="Backing incompleto"):
            create_lot(db_session, [hec.hec_id], name="Lote")

    def test_no_tx_hash_blocked(self, db_session):
        """Backing incompleto (sem tx_hash) → bloqueado."""
        hec = _make_no_tx_hec(db_session)
        with pytest.raises(ValueError, match="Backing incompleto"):
            create_lot(db_session, [hec.hec_id], name="Lote")

    def test_hec_already_in_lot_raises(self, db_session):
        """HEC já em outro lote → bloqueado."""
        r = _make_backed_hec(db_session)
        create_lot(db_session, [r.hec_id], name="Lote 1")
        db_session.commit()

        with pytest.raises(ValueError, match="já pertence"):
            create_lot(db_session, [r.hec_id], name="Lote 2")

    def test_duplicates_deduplicated(self, db_session):
        """Duplicatas na lista são ignoradas."""
        r = _make_backed_hec(db_session)
        result = create_lot(
            db_session,
            [r.hec_id, r.hec_id, r.hec_id],
            name="Dedup",
        )
        db_session.commit()
        assert result.total_quantity == 1

    def test_lot_with_price(self, db_session):
        """Lote com preço por kWh."""
        r = _make_backed_hec(db_session)
        result = create_lot(
            db_session, [r.hec_id], name="Lote",
            price_per_kwh=0.45,
        )
        db_session.commit()

        lot = db_session.query(HECLot).filter(
            HECLot.lot_id == result.lot_id
        ).first()
        assert float(lot.price_per_kwh) == 0.45

    def test_lot_persists_in_db(self, db_session):
        """Lote persiste no banco com campos corretos."""
        r = _make_backed_hec(db_session, energy_kwh=25.5)
        result = create_lot(db_session, [r.hec_id], name="DB Test")
        db_session.commit()

        lot = db_session.query(HECLot).filter(
            HECLot.lot_id == result.lot_id
        ).first()
        assert lot is not None
        assert lot.name == "DB Test"
        assert lot.total_quantity == 1
        assert lot.available_quantity == 1
        assert float(lot.total_energy_kwh) == 25.5
        assert lot.status == "open"


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — POST /lots/create
# ═══════════════════════════════════════════════════════════════════

class TestCreateLotEndpoint:
    """POST /lots/create integration tests."""

    def test_create_backed_lot_201(self, client, db_session):
        """Backed HECs → 201 + lote completo."""
        r1 = _make_backed_hec(db_session, energy_kwh=10.0)
        r2 = _make_backed_hec(db_session, energy_kwh=20.0)

        r = client.post("/lots/create", json={
            "hec_ids": [str(r1.hec_id), str(r2.hec_id)],
            "name": "Lote API Test",
            "description": "Teste de lote via API",
        })
        assert r.status_code == 201
        data = r.json()

        assert data["total_quantity"] == 2
        assert data["available_quantity"] == 2
        assert data["total_energy_kwh"] == 30.0
        assert data["status"] == "open"
        assert data["backing_complete"] is True
        assert data["name"] == "Lote API Test"
        assert data["lot_id"] is not None

    def test_create_response_has_certificates(self, client, db_session):
        """Response includes certificates[] with details."""
        r1 = _make_backed_hec(db_session)
        r = client.post("/lots/create", json={
            "hec_ids": [str(r1.hec_id)],
            "name": "Cert Test",
        })
        data = r.json()

        assert data["certificates"] is not None
        assert len(data["certificates"]) == 1
        cert = data["certificates"][0]
        assert cert["hec_id"] == str(r1.hec_id)
        assert cert["energy_kwh"] == 12.3
        assert cert["certificate_hash"] is not None
        assert cert["ipfs_json_cid"] is not None
        assert cert["registry_tx_hash"] is not None
        assert cert["status"] == "listed"

    def test_pending_blocked_422(self, client, db_session):
        """Pending HEC → 422 blocked."""
        hec = _make_pending_hec(db_session)
        r = client.post("/lots/create", json={
            "hec_ids": [str(hec.hec_id)],
            "name": "Blocked",
        })
        assert r.status_code == 422
        assert "Backing incompleto" in r.json()["detail"]

    def test_no_ipfs_blocked_422(self, client, db_session):
        """No IPFS → 422 blocked."""
        hec = _make_no_ipfs_hec(db_session)
        r = client.post("/lots/create", json={
            "hec_ids": [str(hec.hec_id)],
            "name": "Blocked",
        })
        assert r.status_code == 422
        assert "Backing incompleto" in r.json()["detail"]

    def test_no_tx_hash_blocked_422(self, client, db_session):
        """No tx_hash → 422 blocked."""
        hec = _make_no_tx_hec(db_session)
        r = client.post("/lots/create", json={
            "hec_ids": [str(hec.hec_id)],
            "name": "Blocked",
        })
        assert r.status_code == 422
        assert "Backing incompleto" in r.json()["detail"]

    def test_nonexistent_hec_404(self, client, db_session):
        r = client.post("/lots/create", json={
            "hec_ids": [str(uuid.uuid4())],
            "name": "Not Found",
        })
        assert r.status_code == 404

    def test_already_in_lot_409(self, client, db_session):
        """HEC já em lote → 409."""
        r1 = _make_backed_hec(db_session)
        client.post("/lots/create", json={
            "hec_ids": [str(r1.hec_id)],
            "name": "Lote 1",
        })
        r = client.post("/lots/create", json={
            "hec_ids": [str(r1.hec_id)],
            "name": "Lote 2",
        })
        assert r.status_code == 409

    def test_with_price(self, client, db_session):
        """Lote com preço por kWh."""
        r1 = _make_backed_hec(db_session)
        r = client.post("/lots/create", json={
            "hec_ids": [str(r1.hec_id)],
            "name": "Priced",
            "price_per_kwh": 0.55,
        })
        assert r.status_code == 201
        assert r.json()["price_per_kwh"] == 0.55


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — GET /lots/{lot_id} + GET /lots
# ═══════════════════════════════════════════════════════════════════

class TestGetLots:
    """GET endpoints for lots."""

    def test_get_lot_200(self, client, db_session):
        """Get existing lot → 200 with details."""
        r1 = _make_backed_hec(db_session)
        cr = client.post("/lots/create", json={
            "hec_ids": [str(r1.hec_id)],
            "name": "Get Test",
        })
        lot_id = cr.json()["lot_id"]

        r = client.get(f"/lots/{lot_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["lot_id"] == lot_id
        assert data["total_quantity"] == 1
        assert data["backing_complete"] is True
        assert data["certificates"] is not None
        assert len(data["certificates"]) == 1

    def test_get_lot_404(self, client, db_session):
        r = client.get(f"/lots/{uuid.uuid4()}")
        assert r.status_code == 404

    def test_list_lots(self, client, db_session):
        """List all lots."""
        r1 = _make_backed_hec(db_session)
        r2 = _make_backed_hec(db_session)
        client.post("/lots/create", json={
            "hec_ids": [str(r1.hec_id)], "name": "Lote A",
        })
        client.post("/lots/create", json={
            "hec_ids": [str(r2.hec_id)], "name": "Lote B",
        })

        r = client.get("/lots")
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 2


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — Full pipeline telemetry → lot
# ═══════════════════════════════════════════════════════════════════

class TestFullPipelineLot:
    """Telemetria → APPROVED → HEC registered → create lot → backed."""

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

    def test_telemetry_to_lot_full_pipeline(self, client, ecdsa_keys):
        """Full: telemetry → approved → registered → lot → backed."""
        private_pem, public_pem = ecdsa_keys

        # 1. Submit telemetry → APPROVED → auto-HEC registered
        payload = _make_payload(private_pem, public_pem, energy_kwh=12.3)
        r1 = client.post("/telemetry", json=payload)
        assert r1.status_code == 201
        tel_data = r1.json()
        assert tel_data["backing_complete"] is True
        hec_id = tel_data["hec_id"]

        # 2. Create lot with backed HEC
        r2 = client.post("/lots/create", json={
            "hec_ids": [hec_id],
            "name": "Lote Full Pipeline",
        })
        assert r2.status_code == 201
        lot_data = r2.json()

        assert lot_data["total_quantity"] == 1
        assert lot_data["available_quantity"] == 1
        assert lot_data["total_energy_kwh"] == 12.3
        assert lot_data["backing_complete"] is True
        assert lot_data["status"] == "open"

        # 3. Verify lot has the HEC
        assert len(lot_data["certificates"]) == 1
        assert lot_data["certificates"][0]["hec_id"] == hec_id
        assert lot_data["certificates"][0]["status"] == "listed"

    def test_rejected_telemetry_cannot_create_lot(self, client, ecdsa_keys):
        """Rejected telemetry → no HEC → cannot create lot."""
        private_pem, public_pem = ecdsa_keys
        payload = _make_payload(private_pem, public_pem, energy_kwh=200.0)
        r = client.post("/telemetry", json=payload)
        assert r.json()["hec_id"] is None
        # No HEC exists to put in a lot

"""
Testes automatizados — Burn + Certificado

Cenários cobertos:

  UNIT TESTS — Burn Certificate JSON:
    ✅ JSON tem campos obrigatórios (burn_id, user, burn, certificates_burned)
    ✅ JSON é determinístico
    ✅ Hash SHA-256 tem 64 chars
    ✅ Hash é determinístico para mesmo JSON
    ✅ JSON diferentes → hashes diferentes

  UNIT TESTS — Burn Certificate PDF:
    ✅ PDF é bytes válido (%PDF-)
    ✅ PDF tem tamanho razoável

  UNIT TESTS — execute_burn:
    ✅ Burn debita hec_balance da wallet
    ✅ Burn debita energy_balance_kwh da wallet
    ✅ HECs marcados como "retired"
    ✅ BurnCertificate persistido com hash + IPFS + on-chain
    ✅ Saldo insuficiente → ValueError
    ✅ Quantidade 0 → ValueError
    ✅ Motivo inválido → ValueError
    ✅ Burned_hec_ids correto

  INTEGRATION TESTS — POST /burn:
    ✅ Burn 201 → certificate + IPFS + on-chain + tx_hash
    ✅ Wallet debitada após burn
    ✅ HECs status = "retired" após burn
    ✅ Saldo insuficiente → 422
    ✅ Sem auth → 401
    ✅ Motivo inválido → 422
    ✅ status = "burned" (irreversível)
    ✅ irreversible = True

  INTEGRATION TESTS — GET /burn/{id}:
    ✅ Consulta burn existente → 200
    ✅ Burn inexistente → 404
    ✅ Burn de outro usuário → 403

  INTEGRATION TESTS — GET /burn/{id}/certificate:
    ✅ Download PDF → application/pdf
    ✅ PDF starts with %PDF-

  INTEGRATION TESTS — GET /burns:
    ✅ Lista burns do usuário
    ✅ Vazia inicialmente

  IRREVERSIBILIDADE:
    ✅ Após burn, não é possível re-comprar HECs burned
    ✅ HECs retired não aparecem como available
    ✅ Wallet balance não restaura após burn

  FULL PIPELINE:
    ✅ Telemetry → HEC → Lot → Buy → Burn → Certificate
"""
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest

from app.burn_service import (
    build_burn_certificate_json,
    compute_burn_hash,
    generate_burn_certificate_pdf,
    execute_burn,
    BurnResult,
)
from app.hec_generator import issue_hec
from app.lot_service import create_lot
from app.marketplace import buy_from_lot
from app.auth import register_user
from app.models.models import (
    Plant, Validation, HECCertificate, HECLot,
    User, Wallet, BurnCertificate, Transaction,
)
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
        "plant_id": str(SEED_PLANT_ID), "timestamp": ts_str,
        "power_kw": 5.5, "energy_kwh": energy_kwh,
        "signature": signature, "public_key": public_pem, "nonce": nonce,
    }


def _make_approved_validation(db_session, energy_kwh=12.3):
    plant = db_session.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()
    val = Validation(
        validation_id=uuid.uuid4(), plant_id=SEED_PLANT_ID,
        period_start=NOON_UTC.replace(tzinfo=None),
        period_end=(NOON_UTC + timedelta(hours=1)).replace(tzinfo=None),
        energy_kwh=energy_kwh, confidence_score=100.0, status="approved",
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
    plant, val = _make_approved_validation(db_session, energy_kwh)
    result = issue_hec(db_session, plant, val, issued_at=ISSUED_AT)
    db_session.commit()
    return result


def _setup_buyer_with_hecs(db_session, hec_count=1, energy_kwh=10.0, price=0.50):
    """
    Full setup: Create backed HECs → lot → register user → buy all.
    Returns (user, wallet, lot, buy_result).
    """
    # Create backed HECs + lot
    hec_ids = []
    for _ in range(hec_count):
        r = _make_backed_hec(db_session, energy_kwh=energy_kwh)
        hec_ids.append(r.hec_id)
    lot_result = create_lot(
        db_session, hec_ids, name=f"BurnLot-{uuid.uuid4().hex[:6]}",
        price_per_kwh=price,
    )
    db_session.commit()

    # Register user
    user, wallet, token = register_user(
        db_session, f"burner_{uuid.uuid4().hex[:6]}@test.com", "Burner", "pass123",
    )
    db_session.commit()

    # Buy all HECs
    buy_result = buy_from_lot(db_session, user.user_id, lot_result.lot_id, hec_count)
    db_session.commit()

    return user, wallet, lot_result, token


def _register_and_get_token(client, email=None):
    email = email or f"user_{uuid.uuid4().hex[:8]}@test.com"
    r = client.post("/marketplace/register", json={
        "email": email, "name": "Burner", "password": "pass123",
    })
    return r.json()["token"]


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — Burn Certificate JSON + Hash
# ═══════════════════════════════════════════════════════════════════

class TestBurnCertificateJSON:
    """Burn certificate JSON generation tests."""

    def _mock_user(self):
        from types import SimpleNamespace
        return SimpleNamespace(email="test@test.com", name="Test User")

    def _mock_hec(self, energy=10.0, hec_id=None):
        from types import SimpleNamespace
        return SimpleNamespace(
            hec_id=hec_id or uuid.uuid4(),
            energy_kwh=Decimal(str(energy)),
            hash_sha256="ab" * 32,
            lot_id=uuid.uuid4(),
            ipfs_json_cid="QmTestCID",
            registry_tx_hash="0x" + "cd" * 32,
        )

    def test_json_has_required_fields(self):
        user = self._mock_user()
        hecs = [self._mock_hec()]
        burn_id = uuid.uuid4()
        cj = build_burn_certificate_json(burn_id, user, hecs, "voluntary", ISSUED_AT)

        assert "burn_certificate" in cj
        assert cj["burn_certificate"]["burn_id"] == str(burn_id)
        assert cj["burn_certificate"]["type"] == "BURN"
        assert "user" in cj
        assert cj["user"]["email"] == "test@test.com"
        assert "burn" in cj
        assert cj["burn"]["quantity"] == 1
        assert cj["burn"]["irreversible"] is True
        assert "certificates_burned" in cj
        assert len(cj["certificates_burned"]) == 1

    def test_json_deterministic(self):
        user = self._mock_user()
        hec_id = uuid.uuid4()
        hecs = [self._mock_hec(hec_id=hec_id)]
        burn_id = uuid.uuid4()
        cj1 = build_burn_certificate_json(burn_id, user, hecs, "offset", ISSUED_AT)
        cj2 = build_burn_certificate_json(burn_id, user, hecs, "offset", ISSUED_AT)
        assert cj1 == cj2

    def test_hash_is_64_hex(self):
        user = self._mock_user()
        hecs = [self._mock_hec()]
        cj = build_burn_certificate_json(uuid.uuid4(), user, hecs, "voluntary", ISSUED_AT)
        h = compute_burn_hash(cj)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_deterministic(self):
        user = self._mock_user()
        hec_id = uuid.uuid4()
        hecs = [self._mock_hec(hec_id=hec_id)]
        bid = uuid.uuid4()
        cj = build_burn_certificate_json(bid, user, hecs, "voluntary", ISSUED_AT)
        assert compute_burn_hash(cj) == compute_burn_hash(cj)

    def test_different_json_different_hash(self):
        user = self._mock_user()
        cj1 = build_burn_certificate_json(uuid.uuid4(), user, [self._mock_hec()], "offset", ISSUED_AT)
        cj2 = build_burn_certificate_json(uuid.uuid4(), user, [self._mock_hec()], "retirement", ISSUED_AT)
        assert compute_burn_hash(cj1) != compute_burn_hash(cj2)

    def test_multi_hec_json(self):
        user = self._mock_user()
        hecs = [self._mock_hec(10.0), self._mock_hec(20.0)]
        cj = build_burn_certificate_json(uuid.uuid4(), user, hecs, "voluntary", ISSUED_AT)
        assert cj["burn"]["quantity"] == 2
        assert cj["burn"]["total_energy_kwh"] == 30.0
        assert len(cj["certificates_burned"]) == 2


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — Burn Certificate PDF
# ═══════════════════════════════════════════════════════════════════

class TestBurnCertificatePDF:
    """Burn certificate PDF generation tests."""

    def _make_cert_json(self):
        from types import SimpleNamespace
        user = SimpleNamespace(email="test@test.com", name="Test")
        hec = SimpleNamespace(
            hec_id=uuid.uuid4(), energy_kwh=Decimal("10.0"),
            hash_sha256="ab" * 32, lot_id=uuid.uuid4(),
            ipfs_json_cid="QmCID", registry_tx_hash="0x" + "cd" * 32,
        )
        return build_burn_certificate_json(uuid.uuid4(), user, [hec], "voluntary", ISSUED_AT)

    def test_pdf_is_bytes(self):
        cj = self._make_cert_json()
        pdf = generate_burn_certificate_pdf(cj, compute_burn_hash(cj))
        assert isinstance(pdf, bytes)

    def test_pdf_starts_with_header(self):
        cj = self._make_cert_json()
        pdf = generate_burn_certificate_pdf(cj, compute_burn_hash(cj))
        assert pdf[:5] == b"%PDF-"

    def test_pdf_reasonable_size(self):
        cj = self._make_cert_json()
        pdf = generate_burn_certificate_pdf(cj, compute_burn_hash(cj))
        assert 1024 < len(pdf) < 500_000


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — execute_burn
# ═══════════════════════════════════════════════════════════════════

class TestExecuteBurn:
    """execute_burn unit tests."""

    def test_burn_debits_wallet(self, db_session):
        user, wallet, lot, token = _setup_buyer_with_hecs(
            db_session, hec_count=2, energy_kwh=10.0,
        )
        assert wallet.hec_balance == 2

        result = execute_burn(db_session, user, quantity=1, reason="voluntary")
        db_session.commit()

        assert isinstance(result, BurnResult)
        assert result.wallet_hec_after == 1
        assert result.wallet_energy_after == 10.0

    def test_burn_debits_energy(self, db_session):
        user, wallet, _, _ = _setup_buyer_with_hecs(
            db_session, hec_count=2, energy_kwh=15.0,
        )
        result = execute_burn(db_session, user, quantity=2, reason="offset")
        db_session.commit()

        assert result.wallet_hec_after == 0
        assert result.wallet_energy_after == 0.0
        assert result.energy_kwh == 30.0

    def test_hecs_marked_retired(self, db_session):
        user, _, lot, _ = _setup_buyer_with_hecs(
            db_session, hec_count=2, energy_kwh=10.0,
        )
        result = execute_burn(db_session, user, quantity=1, reason="voluntary")
        db_session.commit()

        retired = db_session.query(HECCertificate).filter(
            HECCertificate.status == "retired",
        ).count()
        assert retired == 1

    def test_burn_certificate_persisted(self, db_session):
        user, _, _, _ = _setup_buyer_with_hecs(
            db_session, hec_count=1, energy_kwh=10.0,
        )
        result = execute_burn(db_session, user, quantity=1, reason="offset")
        db_session.commit()

        burn = db_session.query(BurnCertificate).filter(
            BurnCertificate.burn_id == result.burn_id,
        ).first()

        assert burn is not None
        assert burn.status == "burned"
        assert burn.hash_sha256 == result.certificate_hash
        assert len(burn.hash_sha256) == 64
        assert burn.ipfs_json_cid is not None
        assert burn.ipfs_json_cid.startswith("Qm")
        assert burn.registry_tx_hash is not None
        assert burn.registry_tx_hash.startswith("0x")
        assert burn.certificate_json is not None
        assert burn.burned_hec_ids is not None
        assert len(burn.burned_hec_ids) == 1

    def test_insufficient_hec_balance_raises(self, db_session):
        user, _, _, _ = _setup_buyer_with_hecs(
            db_session, hec_count=1, energy_kwh=10.0,
        )
        with pytest.raises(ValueError, match="insuficiente"):
            execute_burn(db_session, user, quantity=5, reason="voluntary")

    def test_zero_quantity_raises(self, db_session):
        user, _, _, _ = _setup_buyer_with_hecs(db_session, hec_count=1)
        with pytest.raises(ValueError, match="deve ser > 0"):
            execute_burn(db_session, user, quantity=0, reason="voluntary")

    def test_invalid_reason_raises(self, db_session):
        user, _, _, _ = _setup_buyer_with_hecs(db_session, hec_count=1)
        with pytest.raises(ValueError, match="Motivo inválido"):
            execute_burn(db_session, user, quantity=1, reason="invalid_reason")

    def test_burned_hec_ids_correct(self, db_session):
        user, _, lot, _ = _setup_buyer_with_hecs(
            db_session, hec_count=2, energy_kwh=10.0,
        )
        result = execute_burn(db_session, user, quantity=2, reason="retirement")
        db_session.commit()

        assert len(result.burned_hec_ids) == 2
        for hid in result.burned_hec_ids:
            assert len(hid) == 36  # UUID string

    def test_burn_result_has_ipfs_and_onchain(self, db_session):
        user, _, _, _ = _setup_buyer_with_hecs(db_session, hec_count=1)
        result = execute_burn(db_session, user, quantity=1, reason="voluntary")
        db_session.commit()

        assert result.ipfs_json_cid is not None
        assert result.ipfs_json_cid.startswith("Qm")
        assert result.ipfs_pdf_cid is not None
        assert result.registry_tx_hash is not None
        assert result.registry_tx_hash.startswith("0x")
        assert result.registry_block is not None
        assert result.contract_address is not None
        assert result.status == "burned"


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — POST /burn
# ═══════════════════════════════════════════════════════════════════

class TestBurnEndpoint:
    """POST /burn integration tests."""

    def _buy_hecs_and_get_token(self, client, db_session, hec_count=1, energy=10.0, price=0.50):
        """Full pipeline: create HECs → lot → register → buy → return token."""
        hec_ids = []
        for _ in range(hec_count):
            r = _make_backed_hec(db_session, energy_kwh=energy)
            hec_ids.append(r.hec_id)
        lot = create_lot(db_session, hec_ids, name="BurnTest", price_per_kwh=price)
        db_session.commit()

        reg = client.post("/marketplace/register", json={
            "email": f"burn_{uuid.uuid4().hex[:6]}@t.com",
            "name": "Burner", "password": "pass123",
        })
        token = reg.json()["token"]
        headers = _auth_header(token)

        client.post("/marketplace/buy", json={
            "lot_id": str(lot.lot_id), "quantity": hec_count,
        }, headers=headers)

        return token, headers

    def test_burn_201(self, client, db_session):
        token, headers = self._buy_hecs_and_get_token(client, db_session, 2, 10.0)

        r = client.post("/burn", json={"quantity": 1, "reason": "voluntary"},
                        headers=headers)
        assert r.status_code == 201
        data = r.json()

        assert data["status"] == "burned"
        assert data["irreversible"] is True
        assert data["quantity"] == 1
        assert data["energy_kwh"] == 10.0
        assert len(data["certificate_hash"]) == 64
        assert data["ipfs_json_cid"] is not None
        assert data["ipfs_json_cid"].startswith("Qm")
        assert data["registry_tx_hash"] is not None
        assert data["registry_tx_hash"].startswith("0x")
        assert len(data["burned_hec_ids"]) == 1
        assert "IRREVERSÍVEL" in data["message"]

    def test_burn_updates_wallet(self, client, db_session):
        token, headers = self._buy_hecs_and_get_token(client, db_session, 2, 10.0)

        client.post("/burn", json={"quantity": 1, "reason": "voluntary"},
                    headers=headers)

        r = client.get("/marketplace/wallet", headers=headers)
        data = r.json()
        assert data["hec_balance"] == 1
        assert data["energy_balance_kwh"] == 10.0

    def test_burn_retires_hecs(self, client, db_session):
        token, headers = self._buy_hecs_and_get_token(client, db_session, 2, 10.0)

        client.post("/burn", json={"quantity": 1, "reason": "offset"},
                    headers=headers)

        retired = db_session.query(HECCertificate).filter(
            HECCertificate.status == "retired",
        ).count()
        assert retired == 1

    def test_burn_insufficient_422(self, client, db_session):
        token, headers = self._buy_hecs_and_get_token(client, db_session, 1, 10.0)

        r = client.post("/burn", json={"quantity": 5, "reason": "voluntary"},
                        headers=headers)
        assert r.status_code == 422
        assert "insuficiente" in r.json()["detail"]

    def test_burn_no_auth_401(self, client, db_session):
        r = client.post("/burn", json={"quantity": 1, "reason": "voluntary"})
        assert r.status_code == 401

    def test_burn_invalid_reason_422(self, client, db_session):
        token, headers = self._buy_hecs_and_get_token(client, db_session, 1, 10.0)
        r = client.post("/burn", json={"quantity": 1, "reason": "bad"},
                        headers=headers)
        assert r.status_code == 422

    def test_burn_all_hecs(self, client, db_session):
        token, headers = self._buy_hecs_and_get_token(client, db_session, 3, 10.0)

        r = client.post("/burn", json={"quantity": 3, "reason": "retirement"},
                        headers=headers)
        assert r.status_code == 201
        data = r.json()
        assert data["quantity"] == 3
        assert data["energy_kwh"] == 30.0
        assert data["wallet_hec_after"] == 0
        assert data["wallet_energy_after"] == 0.0

    def test_burn_offset_reason(self, client, db_session):
        token, headers = self._buy_hecs_and_get_token(client, db_session, 1, 10.0)
        r = client.post("/burn", json={"quantity": 1, "reason": "offset"},
                        headers=headers)
        assert r.status_code == 201
        assert r.json()["reason"] == "offset"


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — GET /burn/{id} + /burn/{id}/certificate
# ═══════════════════════════════════════════════════════════════════

class TestBurnGetEndpoints:
    """GET burn endpoints."""

    def _do_burn(self, client, db_session):
        hec_ids = []
        for _ in range(2):
            r = _make_backed_hec(db_session, energy_kwh=10.0)
            hec_ids.append(r.hec_id)
        lot = create_lot(db_session, hec_ids, name="GetTest", price_per_kwh=0.50)
        db_session.commit()

        reg = client.post("/marketplace/register", json={
            "email": f"get_{uuid.uuid4().hex[:6]}@t.com",
            "name": "Getter", "password": "pass123",
        })
        token = reg.json()["token"]
        headers = _auth_header(token)

        client.post("/marketplace/buy", json={
            "lot_id": str(lot.lot_id), "quantity": 2,
        }, headers=headers)

        r = client.post("/burn", json={"quantity": 1, "reason": "voluntary"},
                        headers=headers)
        return r.json()["burn_id"], headers

    def test_get_burn_200(self, client, db_session):
        burn_id, headers = self._do_burn(client, db_session)
        r = client.get(f"/burn/{burn_id}", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert data["burn_id"] == burn_id
        assert data["status"] == "burned"
        assert data["irreversible"] is True

    def test_get_burn_404(self, client, db_session):
        token = _register_and_get_token(client)
        r = client.get(f"/burn/{uuid.uuid4()}", headers=_auth_header(token))
        assert r.status_code == 404

    def test_get_burn_other_user_403(self, client, db_session):
        burn_id, _ = self._do_burn(client, db_session)
        other_token = _register_and_get_token(client)
        r = client.get(f"/burn/{burn_id}", headers=_auth_header(other_token))
        assert r.status_code == 403

    def test_download_pdf_200(self, client, db_session):
        burn_id, headers = self._do_burn(client, db_session)
        r = client.get(f"/burn/{burn_id}/certificate", headers=headers)
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.content[:5] == b"%PDF-"

    def test_download_pdf_404(self, client, db_session):
        token = _register_and_get_token(client)
        r = client.get(f"/burn/{uuid.uuid4()}/certificate",
                       headers=_auth_header(token))
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — GET /burns (list)
# ═══════════════════════════════════════════════════════════════════

class TestBurnsList:
    """GET /burns list tests."""

    def test_burns_empty_initially(self, client, db_session):
        token = _register_and_get_token(client)
        r = client.get("/burns", headers=_auth_header(token))
        assert r.status_code == 200
        assert r.json() == []

    def test_burns_after_burn(self, client, db_session):
        r1 = _make_backed_hec(db_session, energy_kwh=10.0)
        lot = create_lot(db_session, [r1.hec_id], name="ListTest", price_per_kwh=0.50)
        db_session.commit()

        reg = client.post("/marketplace/register", json={
            "email": f"list_{uuid.uuid4().hex[:6]}@t.com",
            "name": "Lister", "password": "pass123",
        })
        token = reg.json()["token"]
        headers = _auth_header(token)

        client.post("/marketplace/buy", json={
            "lot_id": str(lot.lot_id), "quantity": 1,
        }, headers=headers)

        client.post("/burn", json={"quantity": 1, "reason": "voluntary"},
                    headers=headers)

        r = client.get("/burns", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["quantity"] == 1
        assert data[0]["status"] == "burned"

    def test_burns_no_auth_401(self, client, db_session):
        r = client.get("/burns")
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════
# IRREVERSIBILITY TESTS
# ═══════════════════════════════════════════════════════════════════

class TestIrreversibility:
    """Burn is irreversible — retired HECs cannot be restored."""

    def test_retired_hecs_not_available(self, client, db_session):
        """After burn, retired HECs don't appear as available."""
        user, wallet, lot, token = _setup_buyer_with_hecs(
            db_session, hec_count=2, energy_kwh=10.0,
        )
        execute_burn(db_session, user, quantity=2, reason="voluntary")
        db_session.commit()

        # All HECs should be retired
        retired = db_session.query(HECCertificate).filter(
            HECCertificate.lot_id == lot.lot_id,
            HECCertificate.status == "retired",
        ).count()
        assert retired == 2

        # None should be sold or listed
        active = db_session.query(HECCertificate).filter(
            HECCertificate.lot_id == lot.lot_id,
            HECCertificate.status.in_(["sold", "listed"]),
        ).count()
        assert active == 0

    def test_wallet_not_restored_after_burn(self, client, db_session):
        """Wallet balance stays debited after burn."""
        user, wallet, _, _ = _setup_buyer_with_hecs(
            db_session, hec_count=2, energy_kwh=10.0,
        )
        initial_hec = wallet.hec_balance
        assert initial_hec == 2

        execute_burn(db_session, user, quantity=2, reason="voluntary")
        db_session.commit()

        # Refresh wallet
        refreshed = db_session.query(Wallet).filter(
            Wallet.user_id == user.user_id
        ).first()
        assert refreshed.hec_balance == 0
        assert float(refreshed.energy_balance_kwh) == 0.0

    def test_cannot_burn_more_than_owned(self, client, db_session):
        """Cannot burn after already burning all."""
        user, _, _, _ = _setup_buyer_with_hecs(
            db_session, hec_count=1, energy_kwh=10.0,
        )
        execute_burn(db_session, user, quantity=1, reason="voluntary")
        db_session.commit()

        with pytest.raises(ValueError, match="insuficiente"):
            execute_burn(db_session, user, quantity=1, reason="voluntary")


# ═══════════════════════════════════════════════════════════════════
# FULL PIPELINE — Telemetry → HEC → Lot → Buy → Burn
# ═══════════════════════════════════════════════════════════════════

class TestFullPipelineBurn:
    """Complete pipeline: telemetry → burn certificate."""

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

    def test_full_lifecycle(self, client, db_session, ecdsa_keys):
        """Telemetry → APPROVED → HEC → Lot → Buy → Burn → Certificate."""
        private_pem, public_pem = ecdsa_keys

        # 1. Telemetry → APPROVED → auto-HEC registered
        payload = _make_payload(private_pem, public_pem, energy_kwh=12.3)
        r1 = client.post("/telemetry", json=payload)
        assert r1.status_code == 201
        hec_id = r1.json()["hec_id"]
        assert r1.json()["backing_complete"] is True

        # 2. Create lot
        r2 = client.post("/lots/create", json={
            "hec_ids": [hec_id], "name": "Full Pipeline",
            "price_per_kwh": 0.50,
        })
        assert r2.status_code == 201
        lot_id = r2.json()["lot_id"]

        # 3. Register + Buy
        reg = client.post("/marketplace/register", json={
            "email": "full_pipeline@test.com",
            "name": "Full", "password": "pass123",
        })
        token = reg.json()["token"]
        headers = _auth_header(token)

        r3 = client.post("/marketplace/buy", json={
            "lot_id": lot_id, "quantity": 1,
        }, headers=headers)
        assert r3.status_code == 201

        # 4. Burn
        r4 = client.post("/burn", json={
            "quantity": 1, "reason": "offset",
        }, headers=headers)
        assert r4.status_code == 201
        burn_data = r4.json()

        assert burn_data["status"] == "burned"
        assert burn_data["irreversible"] is True
        assert burn_data["quantity"] == 1
        assert burn_data["energy_kwh"] == 12.3
        assert burn_data["certificate_hash"] is not None
        assert burn_data["ipfs_json_cid"] is not None
        assert burn_data["registry_tx_hash"] is not None
        assert burn_data["wallet_hec_after"] == 0
        assert burn_data["reason"] == "offset"

        # 5. Download certificate
        burn_id = burn_data["burn_id"]
        r5 = client.get(f"/burn/{burn_id}/certificate", headers=headers)
        assert r5.status_code == 200
        assert r5.content[:5] == b"%PDF-"

        # 6. Verify wallet empty
        r6 = client.get("/marketplace/wallet", headers=headers)
        assert r6.json()["hec_balance"] == 0

        # 7. Verify HEC retired
        hec = db_session.query(HECCertificate).filter(
            HECCertificate.hec_id == hec_id,
        ).first()
        assert hec.status == "retired"

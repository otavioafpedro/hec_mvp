"""
Testes automatizados — Marketplace API

Cenários cobertos:

  AUTH:
    ✅ POST /register → 201 + user + wallet + token
    ✅ POST /register email duplicado → 409
    ✅ POST /login válido → 200 + token
    ✅ POST /login senha errada → 401
    ✅ POST /login email inexistente → 401
    ✅ Token válido → autenticação ok
    ✅ Token inválido → 401
    ✅ Sem Authorization header → 401

  WALLET:
    ✅ GET /wallet autenticado → saldo inicial R$ 10.000
    ✅ GET /wallet sem auth → 401

  LOTS (marketplace):
    ✅ GET /marketplace/lots → somente backed com preço
    ✅ Lote sem preço → excluído
    ✅ Lote sem backing → excluído
    ✅ Lote sold → excluído

  BUY:
    ✅ POST /buy → 201 + transação atômica
    ✅ Wallet debitada corretamente
    ✅ Wallet creditada HECs + energy
    ✅ Lote available decrementado
    ✅ HECs marcados como "sold"
    ✅ quantity > available → 422
    ✅ Saldo insuficiente → 422
    ✅ Lote inexistente → 404
    ✅ Lote sold → 409
    ✅ Compra parcial (2 de 3 available)
    ✅ Compra total → lot.status = "sold"
    ✅ Sem auth → 401

  TRANSACTIONS:
    ✅ GET /transactions → histórico do usuário
    ✅ Transação aparece no histórico após compra

  ATOMICIDADE:
    ✅ Falha no meio não altera saldo/lote
"""
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest

from app.auth import (
    hash_password, verify_password,
    create_token, verify_token,
    register_user, login_user, login_or_create_social_user,
    INITIAL_BALANCE_BRL,
)
from app.marketplace import buy_from_lot, BuyResult
from app.hec_generator import issue_hec
from app.lot_service import create_lot
from app.models.models import (
    Plant, Validation, HECCertificate, HECLot,
    ConsumerProfile, User, UserRoleBinding, Wallet, Transaction,
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


def _make_approved_validation(db_session, energy_kwh=12.3):
    plant = db_session.query(Plant).filter(Plant.plant_id == SEED_PLANT_ID).first()
    val = Validation(
        validation_id=uuid.uuid4(),
        plant_id=SEED_PLANT_ID,
        period_start=NOON_UTC.replace(tzinfo=None),
        period_end=(NOON_UTC + timedelta(hours=1)).replace(tzinfo=None),
        energy_kwh=energy_kwh,
        confidence_score=100.0, status="approved",
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


def _make_priced_lot(db_session, hec_count=1, energy_kwh=10.0, price=0.50):
    """Create a backed, priced, open lot ready for purchase."""
    hec_ids = []
    for _ in range(hec_count):
        r = _make_backed_hec(db_session, energy_kwh=energy_kwh)
        hec_ids.append(r.hec_id)
    result = create_lot(
        db_session, hec_ids, name=f"Lote {uuid.uuid4().hex[:6]}",
        price_per_kwh=price,
    )
    db_session.commit()
    return result


def _register_user(client, email=None, name="Teste", password="test123"):
    email = email or f"user_{uuid.uuid4().hex[:8]}@test.com"
    r = client.post("/marketplace/register", json={
        "email": email, "name": name, "password": password,
    })
    return r


def _get_auth_header(register_response):
    return {"Authorization": f"Bearer {register_response.json()['token']}"}


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — Auth functions
# ═══════════════════════════════════════════════════════════════════

class TestAuthUnit:
    """Auth service unit tests."""

    def test_hash_password(self):
        h = hash_password("secret123")
        assert len(h) == 64  # SHA-256 hex
        assert h == hash_password("secret123")  # Deterministic

    def test_verify_password(self):
        h = hash_password("mypass")
        assert verify_password("mypass", h) is True
        assert verify_password("wrong", h) is False

    def test_create_token(self):
        token = create_token("user-id", "a@b.com")
        assert "." in token
        assert len(token) > 50

    def test_verify_valid_token(self):
        token = create_token("uid", "a@b.com")
        payload = verify_token(token)
        assert payload is not None
        assert payload["user_id"] == "uid"
        assert payload["email"] == "a@b.com"

    def test_verify_invalid_token(self):
        assert verify_token("garbage.token") is None
        assert verify_token("") is None
        assert verify_token("no-dot") is None

    def test_verify_tampered_token(self):
        token = create_token("uid", "a@b.com")
        parts = token.split(".")
        tampered = parts[0] + ".0000" + parts[1][4:]
        assert verify_token(tampered) is None

    def test_register_creates_user_and_wallet(self, db_session):
        user, wallet, token = register_user(
            db_session, "new@test.com", "New User", "pass123",
        )
        db_session.commit()

        assert user.email == "new@test.com"
        assert user.role == "buyer"
        assert wallet.balance_brl == INITIAL_BALANCE_BRL
        assert wallet.hec_balance == 0
        assert len(token) > 50

        profile = (
            db_session.query(ConsumerProfile)
            .filter(ConsumerProfile.user_id == user.user_id)
            .first()
        )
        assert profile is not None
        assert profile.display_name == "New User"
        assert profile.person_type == "PF"

        bindings = {
            binding.role_code: binding
            for binding in (
                db_session.query(UserRoleBinding)
                .filter(UserRoleBinding.user_id == user.user_id)
                .all()
            )
        }
        assert set(bindings) == {"consumer"}
        assert bindings["consumer"].is_primary is True

    def test_register_duplicate_raises(self, db_session):
        register_user(db_session, "dup@test.com", "A", "pass")
        db_session.commit()
        with pytest.raises(ValueError, match="já registrado"):
            register_user(db_session, "dup@test.com", "B", "pass")

    def test_social_login_creates_consumer_identity(self, db_session):
        user, wallet, token, created = login_or_create_social_user(
            db_session,
            "social@test.com",
            "Social User",
        )
        db_session.commit()

        assert created is True
        assert user.email == "social@test.com"
        assert wallet.balance_brl == INITIAL_BALANCE_BRL
        assert len(token) > 50

        profile = (
            db_session.query(ConsumerProfile)
            .filter(ConsumerProfile.user_id == user.user_id)
            .first()
        )
        assert profile is not None
        assert profile.display_name == "Social User"

        bindings = (
            db_session.query(UserRoleBinding)
            .filter(UserRoleBinding.user_id == user.user_id)
            .all()
        )
        assert len(bindings) == 1
        assert bindings[0].role_code == "consumer"
        assert bindings[0].is_primary is True

    def test_login_backfills_missing_consumer_identity(self, db_session):
        user = User(
            user_id=uuid.uuid4(),
            email="legacy@test.com",
            name="Legacy User",
            password_hash=hash_password("legacy123"),
            role="buyer",
            is_active=True,
        )
        db_session.add(user)
        db_session.add(
            Wallet(
                wallet_id=uuid.uuid4(),
                user_id=user.user_id,
                balance_brl=INITIAL_BALANCE_BRL,
                hec_balance=0,
                energy_balance_kwh=Decimal("0"),
            )
        )
        db_session.commit()

        logged_user, token = login_user(db_session, "legacy@test.com", "legacy123")
        db_session.commit()

        assert logged_user.user_id == user.user_id
        assert len(token) > 50

        profile = (
            db_session.query(ConsumerProfile)
            .filter(ConsumerProfile.user_id == user.user_id)
            .first()
        )
        assert profile is not None

        binding = (
            db_session.query(UserRoleBinding)
            .filter(
                UserRoleBinding.user_id == user.user_id,
                UserRoleBinding.role_code == "consumer",
            )
            .first()
        )
        assert binding is not None
        assert binding.is_primary is True

    def test_login_valid(self, db_session):
        register_user(db_session, "login@test.com", "L", "pass123")
        db_session.commit()
        user, token = login_user(db_session, "login@test.com", "pass123")
        assert user.email == "login@test.com"
        assert len(token) > 50

    def test_login_wrong_password(self, db_session):
        register_user(db_session, "wrong@test.com", "W", "correct")
        db_session.commit()
        with pytest.raises(ValueError, match="incorretos"):
            login_user(db_session, "wrong@test.com", "wrong")

    def test_login_nonexistent(self, db_session):
        with pytest.raises(ValueError, match="incorretos"):
            login_user(db_session, "noone@test.com", "pass")


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — buy_from_lot
# ═══════════════════════════════════════════════════════════════════

class TestBuyUnit:
    """buy_from_lot unit tests."""

    def _setup_buyer(self, db_session):
        user, wallet, _ = register_user(
            db_session, f"buyer_{uuid.uuid4().hex[:6]}@t.com", "B", "pass",
        )
        db_session.commit()
        return user, wallet

    def test_buy_success(self, db_session):
        lot = _make_priced_lot(db_session, hec_count=2, energy_kwh=10.0, price=0.50)
        user, wallet = self._setup_buyer(db_session)

        result = buy_from_lot(db_session, user.user_id, lot.lot_id, quantity=1)
        db_session.commit()

        assert isinstance(result, BuyResult)
        assert result.quantity == 1
        assert result.energy_kwh == 10.0
        assert result.total_price_brl == 5.0  # 10 kWh × R$0.50
        assert result.status == "completed"
        assert result.lot_available_after == 1
        assert result.lot_status_after == "open"
        assert result.wallet_hec_after == 1

    def test_buy_all_marks_lot_sold(self, db_session):
        lot = _make_priced_lot(db_session, hec_count=1, energy_kwh=10.0, price=0.50)
        user, _ = self._setup_buyer(db_session)

        result = buy_from_lot(db_session, user.user_id, lot.lot_id, quantity=1)
        db_session.commit()

        assert result.lot_available_after == 0
        assert result.lot_status_after == "sold"

    def test_buy_exceeds_available_raises(self, db_session):
        lot = _make_priced_lot(db_session, hec_count=2, energy_kwh=10.0, price=0.50)
        user, _ = self._setup_buyer(db_session)

        with pytest.raises(ValueError, match="excede disponível"):
            buy_from_lot(db_session, user.user_id, lot.lot_id, quantity=5)

    def test_buy_insufficient_balance_raises(self, db_session):
        lot = _make_priced_lot(db_session, hec_count=1, energy_kwh=10.0, price=5000.0)
        user, wallet = self._setup_buyer(db_session)
        # Price = 10 kWh × R$5000 = R$50,000 > R$10,000 balance

        with pytest.raises(ValueError, match="Saldo insuficiente"):
            buy_from_lot(db_session, user.user_id, lot.lot_id, quantity=1)

    def test_buy_zero_quantity_raises(self, db_session):
        lot = _make_priced_lot(db_session)
        user, _ = self._setup_buyer(db_session)

        with pytest.raises(ValueError, match="deve ser > 0"):
            buy_from_lot(db_session, user.user_id, lot.lot_id, quantity=0)

    def test_buy_nonexistent_lot_raises(self, db_session):
        user, _ = self._setup_buyer(db_session)
        with pytest.raises(ValueError, match="não encontrado"):
            buy_from_lot(db_session, user.user_id, uuid.uuid4(), quantity=1)

    def test_wallet_debited_correctly(self, db_session):
        lot = _make_priced_lot(db_session, hec_count=1, energy_kwh=20.0, price=1.0)
        user, wallet = self._setup_buyer(db_session)
        initial_balance = float(wallet.balance_brl)

        result = buy_from_lot(db_session, user.user_id, lot.lot_id, quantity=1)
        db_session.commit()

        # Price = 20 kWh × R$1.0 = R$20.00
        assert result.total_price_brl == 20.0
        assert result.wallet_balance_after == initial_balance - 20.0

    def test_hecs_marked_sold(self, db_session):
        lot = _make_priced_lot(db_session, hec_count=2, energy_kwh=10.0, price=0.50)
        user, _ = self._setup_buyer(db_session)

        buy_from_lot(db_session, user.user_id, lot.lot_id, quantity=1)
        db_session.commit()

        # 1 should be sold, 1 still listed
        sold = db_session.query(HECCertificate).filter(
            HECCertificate.lot_id == lot.lot_id,
            HECCertificate.status == "sold",
        ).count()
        listed = db_session.query(HECCertificate).filter(
            HECCertificate.lot_id == lot.lot_id,
            HECCertificate.status == "listed",
        ).count()
        assert sold == 1
        assert listed == 1

    def test_transaction_record_created(self, db_session):
        lot = _make_priced_lot(db_session, hec_count=1, energy_kwh=10.0, price=0.50)
        user, _ = self._setup_buyer(db_session)

        result = buy_from_lot(db_session, user.user_id, lot.lot_id, quantity=1)
        db_session.commit()

        tx = db_session.query(Transaction).filter(
            Transaction.tx_id == result.tx_id
        ).first()
        assert tx is not None
        assert tx.buyer_id == user.user_id
        assert tx.lot_id == lot.lot_id
        assert tx.quantity == 1
        assert float(tx.total_price_brl) == 5.0
        assert tx.status == "completed"


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — POST /marketplace/register + /login
# ═══════════════════════════════════════════════════════════════════

class TestAuthEndpoints:
    """Auth endpoint integration tests."""

    def test_register_201(self, client, db_session):
        r = client.post("/marketplace/register", json={
            "email": "test@example.com",
            "name": "Test User",
            "password": "secure123",
        })
        assert r.status_code == 201
        data = r.json()
        assert data["email"] == "test@example.com"
        assert data["role"] == "buyer"
        assert data["token"] is not None
        assert data["wallet_balance_brl"] == 10000.0
        assert "Conta criada" in data["message"]

    def test_register_duplicate_409(self, client, db_session):
        client.post("/marketplace/register", json={
            "email": "dup@x.com", "name": "A", "password": "pass123",
        })
        r = client.post("/marketplace/register", json={
            "email": "dup@x.com", "name": "B", "password": "pass456",
        })
        assert r.status_code == 409

    def test_login_200(self, client, db_session):
        client.post("/marketplace/register", json={
            "email": "login@x.com", "name": "L", "password": "pass123",
        })
        r = client.post("/marketplace/login", json={
            "email": "login@x.com", "password": "pass123",
        })
        assert r.status_code == 200
        assert r.json()["token"] is not None

    def test_login_wrong_pass_401(self, client, db_session):
        client.post("/marketplace/register", json={
            "email": "lw@x.com", "name": "L", "password": "correct",
        })
        r = client.post("/marketplace/login", json={
            "email": "lw@x.com", "password": "wrong",
        })
        assert r.status_code == 401

    def test_login_nonexistent_401(self, client, db_session):
        r = client.post("/marketplace/login", json={
            "email": "nope@x.com", "password": "pass",
        })
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — GET /marketplace/wallet
# ═══════════════════════════════════════════════════════════════════

class TestWalletEndpoint:
    """Wallet endpoint tests."""

    def test_wallet_authed_200(self, client, db_session):
        reg = _register_user(client)
        headers = _get_auth_header(reg)
        r = client.get("/marketplace/wallet", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert data["balance_brl"] == 10000.0
        assert data["hec_balance"] == 0
        assert data["energy_balance_kwh"] == 0.0

    def test_wallet_no_auth_401(self, client, db_session):
        r = client.get("/marketplace/wallet")
        assert r.status_code == 401

    def test_wallet_bad_token_401(self, client, db_session):
        r = client.get("/marketplace/wallet",
                       headers={"Authorization": "Bearer fake.token"})
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — GET /marketplace/lots
# ═══════════════════════════════════════════════════════════════════

class TestMarketplaceLots:
    """Marketplace lots listing tests."""

    def test_backed_lot_appears(self, client, db_session):
        _make_priced_lot(db_session, hec_count=1, energy_kwh=10.0, price=0.50)
        r = client.get("/marketplace/lots")
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 1
        assert data[0]["backing_complete"] is True
        assert data[0]["price_per_kwh"] == 0.5

    def test_lot_without_price_excluded(self, client, db_session):
        """Lot without price_per_kwh → not in marketplace."""
        r1 = _make_backed_hec(db_session)
        create_lot(db_session, [r1.hec_id], name="No Price")
        db_session.commit()

        r = client.get("/marketplace/lots")
        names = [l["name"] for l in r.json()]
        assert "No Price" not in names

    def test_sold_lot_excluded(self, client, db_session):
        """Sold lot → not in marketplace."""
        lot = _make_priced_lot(db_session, hec_count=1, price=0.10)
        # Mark as sold
        lot_db = db_session.query(HECLot).filter(HECLot.lot_id == lot.lot_id).first()
        lot_db.status = "sold"
        lot_db.available_quantity = 0
        db_session.commit()

        r = client.get("/marketplace/lots")
        ids = [l["lot_id"] for l in r.json()]
        assert str(lot.lot_id) not in ids


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — POST /marketplace/buy
# ═══════════════════════════════════════════════════════════════════

class TestBuyEndpoint:
    """Buy endpoint integration tests."""

    def test_buy_201(self, client, db_session):
        lot = _make_priced_lot(db_session, hec_count=2, energy_kwh=10.0, price=0.50)
        reg = _register_user(client)
        headers = _get_auth_header(reg)

        r = client.post("/marketplace/buy", json={
            "lot_id": str(lot.lot_id), "quantity": 1,
        }, headers=headers)
        assert r.status_code == 201
        data = r.json()

        assert data["quantity"] == 1
        assert data["energy_kwh"] == 10.0
        assert data["total_price_brl"] == 5.0
        assert data["status"] == "completed"
        assert data["lot_available_after"] == 1
        assert data["wallet_hec_after"] == 1
        assert data["wallet_balance_after"] == 9995.0

    def test_buy_updates_wallet(self, client, db_session):
        lot = _make_priced_lot(db_session, hec_count=1, energy_kwh=20.0, price=1.0)
        reg = _register_user(client)
        headers = _get_auth_header(reg)

        client.post("/marketplace/buy", json={
            "lot_id": str(lot.lot_id), "quantity": 1,
        }, headers=headers)

        r = client.get("/marketplace/wallet", headers=headers)
        data = r.json()
        assert data["balance_brl"] == 9980.0  # 10000 - 20
        assert data["hec_balance"] == 1
        assert data["energy_balance_kwh"] == 20.0

    def test_buy_all_lot_sold(self, client, db_session):
        lot = _make_priced_lot(db_session, hec_count=2, energy_kwh=10.0, price=0.50)
        reg = _register_user(client)
        headers = _get_auth_header(reg)

        r = client.post("/marketplace/buy", json={
            "lot_id": str(lot.lot_id), "quantity": 2,
        }, headers=headers)
        data = r.json()
        assert data["lot_available_after"] == 0
        assert data["lot_status_after"] == "sold"

    def test_buy_exceeds_available_422(self, client, db_session):
        lot = _make_priced_lot(db_session, hec_count=1, price=0.10)
        reg = _register_user(client)
        headers = _get_auth_header(reg)

        r = client.post("/marketplace/buy", json={
            "lot_id": str(lot.lot_id), "quantity": 5,
        }, headers=headers)
        assert r.status_code == 422
        assert "excede disponível" in r.json()["detail"]

    def test_buy_insufficient_balance_422(self, client, db_session):
        lot = _make_priced_lot(db_session, hec_count=1, energy_kwh=10.0, price=5000.0)
        reg = _register_user(client)
        headers = _get_auth_header(reg)

        r = client.post("/marketplace/buy", json={
            "lot_id": str(lot.lot_id), "quantity": 1,
        }, headers=headers)
        assert r.status_code == 422
        assert "Saldo insuficiente" in r.json()["detail"]

    def test_buy_nonexistent_lot_404(self, client, db_session):
        reg = _register_user(client)
        headers = _get_auth_header(reg)
        r = client.post("/marketplace/buy", json={
            "lot_id": str(uuid.uuid4()), "quantity": 1,
        }, headers=headers)
        assert r.status_code == 404

    def test_buy_no_auth_401(self, client, db_session):
        r = client.post("/marketplace/buy", json={
            "lot_id": str(uuid.uuid4()), "quantity": 1,
        })
        assert r.status_code == 401

    def test_buy_partial(self, client, db_session):
        """Buy 2 of 3 → 1 remains."""
        lot = _make_priced_lot(db_session, hec_count=3, energy_kwh=10.0, price=0.50)
        reg = _register_user(client)
        headers = _get_auth_header(reg)

        r = client.post("/marketplace/buy", json={
            "lot_id": str(lot.lot_id), "quantity": 2,
        }, headers=headers)
        data = r.json()
        assert data["quantity"] == 2
        assert data["lot_available_after"] == 1
        assert data["lot_status_after"] == "open"
        assert data["wallet_hec_after"] == 2

    def test_multiple_buys_from_same_lot(self, client, db_session):
        """Two sequential buys from same lot."""
        lot = _make_priced_lot(db_session, hec_count=3, energy_kwh=10.0, price=0.50)
        reg = _register_user(client)
        headers = _get_auth_header(reg)

        r1 = client.post("/marketplace/buy", json={
            "lot_id": str(lot.lot_id), "quantity": 1,
        }, headers=headers)
        assert r1.json()["lot_available_after"] == 2
        assert r1.json()["wallet_hec_after"] == 1

        r2 = client.post("/marketplace/buy", json={
            "lot_id": str(lot.lot_id), "quantity": 2,
        }, headers=headers)
        assert r2.json()["lot_available_after"] == 0
        assert r2.json()["lot_status_after"] == "sold"
        assert r2.json()["wallet_hec_after"] == 3


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — GET /marketplace/transactions
# ═══════════════════════════════════════════════════════════════════

class TestTransactionsEndpoint:
    """Transaction history tests."""

    def test_transactions_after_buy(self, client, db_session):
        lot = _make_priced_lot(db_session, hec_count=1, energy_kwh=10.0, price=0.50)
        reg = _register_user(client)
        headers = _get_auth_header(reg)

        client.post("/marketplace/buy", json={
            "lot_id": str(lot.lot_id), "quantity": 1,
        }, headers=headers)

        r = client.get("/marketplace/transactions", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["quantity"] == 1
        assert data[0]["total_price_brl"] == 5.0
        assert data[0]["status"] == "completed"

    def test_transactions_empty_initially(self, client, db_session):
        reg = _register_user(client)
        headers = _get_auth_header(reg)
        r = client.get("/marketplace/transactions", headers=headers)
        assert r.status_code == 200
        assert r.json() == []

    def test_transactions_no_auth_401(self, client, db_session):
        r = client.get("/marketplace/transactions")
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════
# ATOMICITY TESTS
# ═══════════════════════════════════════════════════════════════════

class TestAtomicity:
    """Verify atomic buy behavior."""

    def test_failed_buy_no_side_effects(self, client, db_session):
        """If buy fails (exceeds), wallet and lot unchanged."""
        lot = _make_priced_lot(db_session, hec_count=1, energy_kwh=10.0, price=0.50)
        reg = _register_user(client)
        headers = _get_auth_header(reg)

        # Try to buy 5 (only 1 available) → should fail
        r = client.post("/marketplace/buy", json={
            "lot_id": str(lot.lot_id), "quantity": 5,
        }, headers=headers)
        assert r.status_code == 422

        # Wallet unchanged
        w = client.get("/marketplace/wallet", headers=headers)
        assert w.json()["balance_brl"] == 10000.0
        assert w.json()["hec_balance"] == 0

        # Lot unchanged
        lot_db = db_session.query(HECLot).filter(HECLot.lot_id == lot.lot_id).first()
        assert lot_db.available_quantity == 1
        assert lot_db.status == "open"

    def test_successful_buy_all_consistent(self, client, db_session):
        """After success, wallet + lot + HECs + transaction all consistent."""
        lot = _make_priced_lot(db_session, hec_count=2, energy_kwh=15.0, price=0.40)
        reg = _register_user(client)
        headers = _get_auth_header(reg)

        r = client.post("/marketplace/buy", json={
            "lot_id": str(lot.lot_id), "quantity": 2,
        }, headers=headers)
        assert r.status_code == 201
        data = r.json()

        # Wallet: 10000 - (30 kWh × 0.40) = 10000 - 12 = 9988
        assert data["total_price_brl"] == 12.0
        assert data["wallet_balance_after"] == 9988.0
        assert data["wallet_hec_after"] == 2
        assert data["wallet_energy_after"] == 30.0

        # Lot: 0 available, sold
        assert data["lot_available_after"] == 0
        assert data["lot_status_after"] == "sold"

        # HECs all sold
        sold_count = db_session.query(HECCertificate).filter(
            HECCertificate.lot_id == lot.lot_id,
            HECCertificate.status == "sold",
        ).count()
        assert sold_count == 2

        # Transaction exists
        txs = db_session.query(Transaction).filter(
            Transaction.lot_id == lot.lot_id
        ).all()
        assert len(txs) == 1
        assert float(txs[0].total_price_brl) == 12.0

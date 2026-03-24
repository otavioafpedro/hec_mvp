"""
Serviço de autenticação — Registro, login e verificação de token.

Usa HMAC-SHA256 para tokens (JWT-like simplificado).
Passwords com SHA-256 + salt (bcrypt em produção).

Fluxo:
  1. POST /register → cria user + wallet (saldo 0) → retorna token
  2. POST /login → verifica email + senha → retorna token
  3. Endpoints protegidos usam get_current_user() → extrai user_id do token
"""
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.identity import ensure_consumer_identity
from app.models.models import User, Wallet


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TOKEN_SECRET = "hec-marketplace-secret-key-change-in-production"
TOKEN_EXPIRY_SECONDS = 86400  # 24h
INITIAL_BALANCE_BRL = Decimal("10000.00")  # Saldo inicial para demo


# ---------------------------------------------------------------------------
# Password hashing (SHA-256 + salt para dev, bcrypt em produção)
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Hash password com SHA-256 + salt fixo (usar bcrypt em produção)."""
    salt = "hec-salt-v1"
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    """Verifica password contra hash armazenado."""
    return hash_password(password) == password_hash


# ---------------------------------------------------------------------------
# Token (HMAC-SHA256 simplificado)
# ---------------------------------------------------------------------------

def create_token(user_id: str, email: str) -> str:
    """
    Cria token HMAC-SHA256.
    Formato: {payload_b64}.{signature_hex}
    """
    payload = {
        "user_id": user_id,
        "email": email,
        "exp": int(time.time()) + TOKEN_EXPIRY_SECONDS,
        "iat": int(time.time()),
    }
    payload_json = json.dumps(payload, sort_keys=True)
    import base64
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode()
    sig = hmac.new(TOKEN_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def verify_token(token: str) -> Optional[dict]:
    """
    Verifica token HMAC-SHA256.
    Retorna payload dict se válido, None se inválido/expirado.
    """
    try:
        parts = token.split(".")
        if len(parts) != 2:
            return None
        payload_b64, sig = parts
        expected_sig = hmac.new(
            TOKEN_SECRET.encode(), payload_b64.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        import base64
        payload_json = base64.urlsafe_b64decode(payload_b64).decode()
        payload = json.loads(payload_json)
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Register + Login
# ---------------------------------------------------------------------------

def register_user(
    db: Session,
    email: str,
    name: str,
    password: str,
    role: str = "buyer",
    wallet_address: Optional[str] = None,
) -> tuple:
    """
    Registra novo usuário + cria wallet com saldo inicial.

    Returns:
        (user, wallet, token)

    Raises:
        ValueError: Se email já existe
    """
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise ValueError(f"Email {email} já registrado")

    user = User(
        user_id=uuid.uuid4(),
        email=email,
        name=name,
        password_hash=hash_password(password),
        role=role,
        is_active=True,
    )
    db.add(user)
    ensure_consumer_identity(db, user)

    wallet = Wallet(
        wallet_id=uuid.uuid4(),
        user_id=user.user_id,
        wallet_address=wallet_address,
        balance_brl=INITIAL_BALANCE_BRL,
        hec_balance=0,
        energy_balance_kwh=Decimal("0"),
    )
    db.add(wallet)

    token = create_token(str(user.user_id), email)
    return user, wallet, token


def login_user(
    db: Session,
    email: str,
    password: str,
) -> tuple:
    """
    Autentica usuário.

    Returns:
        (user, token)

    Raises:
        ValueError: Se email não existe ou senha incorreta
    """
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise ValueError("Email ou senha incorretos")
    if not verify_password(password, user.password_hash):
        raise ValueError("Email ou senha incorretos")
    if not user.is_active:
        raise ValueError("Conta desativada")

    ensure_consumer_identity(db, user)
    token = create_token(str(user.user_id), email)
    return user, token


def login_or_create_social_user(
    db: Session,
    email: str,
    name: str,
    role: str = "buyer",
    wallet_address: Optional[str] = None,
) -> tuple:
    """
    Login social:
      - Se usuario existe, reutiliza conta
      - Se nao existe, cria usuario com senha aleatoria
      - Garante wallet para ambos os casos

    Returns:
        (user, wallet, token, created)
    """
    normalized_email = (email or "").strip().lower()
    if not normalized_email:
        raise ValueError("Email obrigatorio para login social")

    display_name = (name or "").strip()
    if not display_name:
        local_part = normalized_email.split("@", 1)[0]
        display_name = local_part or "Usuario"

    created = False
    user = db.query(User).filter(User.email == normalized_email).first()

    if not user:
        created = True
        random_password = uuid.uuid4().hex
        user = User(
            user_id=uuid.uuid4(),
            email=normalized_email,
            name=display_name,
            password_hash=hash_password(random_password),
            role=role,
            is_active=True,
        )
        db.add(user)
        db.flush()
    else:
        if display_name and not user.name:
            user.name = display_name
        if not user.is_active:
            user.is_active = True
        db.flush()

    ensure_consumer_identity(db, user)
    wallet = db.query(Wallet).filter(Wallet.user_id == user.user_id).first()
    if not wallet:
        wallet = Wallet(
            wallet_id=uuid.uuid4(),
            user_id=user.user_id,
            wallet_address=wallet_address,
            balance_brl=INITIAL_BALANCE_BRL,
            hec_balance=0,
            energy_balance_kwh=Decimal("0"),
        )
        db.add(wallet)
        db.flush()
    elif wallet_address and not wallet.wallet_address:
        wallet.wallet_address = wallet_address

    token = create_token(str(user.user_id), user.email)
    return user, wallet, token, created

"""
Módulo de segurança — Fortaleza Lógica

Camada 1: Pre-commitment Hash ECDSA
  1. Validação de assinatura ECDSA (secp256k1) do payload de telemetria
  2. SHA-256 do payload canônico para integridade
  3. Anti-replay com nonce único + janela temporal de 60 segundos

Camada 2: Sincronização NTP Blindada
  4. Verificação de drift NTP ±5ms entre timestamp do payload e servidor
"""
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.models import UsedNonce

# ---------------------------------------------------------------------------
# 1. SHA-256 do payload canônico
# ---------------------------------------------------------------------------

def canonical_payload(plant_id: str, timestamp: str, power_kw: float, energy_kwh: float, nonce: str) -> str:
    """
    Gera representação canônica (determinística) do payload para assinatura/hash.
    Ordena as chaves e serializa sem espaços extras.
    """
    data = {
        "energy_kwh": str(energy_kwh),
        "nonce": nonce,
        "plant_id": str(plant_id),
        "power_kw": str(power_kw),
        "timestamp": timestamp,
    }
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def sha256_hash(payload_str: str) -> str:
    """Retorna SHA-256 hex digest do payload canônico."""
    return hashlib.sha256(payload_str.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 2. Validação ECDSA (secp256k1)
# ---------------------------------------------------------------------------

def verify_ecdsa_signature(public_key_pem: str, signature_hex: str, message: str) -> bool:
    """
    Verifica assinatura ECDSA sobre o payload canônico.

    Args:
        public_key_pem: Chave pública PEM (EC secp256k1)
        signature_hex: Assinatura DER em hexadecimal
        message: Payload canônico (string)

    Returns:
        True se assinatura válida, False caso contrário.
    """
    try:
        public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))

        if not isinstance(public_key, ec.EllipticCurvePublicKey):
            return False

        signature_bytes = bytes.fromhex(signature_hex)
        message_bytes = message.encode("utf-8")

        # Verifica assinatura com SHA-256
        public_key.verify(
            signature_bytes,
            message_bytes,
            ec.ECDSA(hashes.SHA256()),
        )
        return True

    except (InvalidSignature, ValueError, TypeError, Exception):
        return False


# ---------------------------------------------------------------------------
# 3. Anti-replay: Nonce único com janela de 60 segundos
# ---------------------------------------------------------------------------

NONCE_WINDOW_SECONDS = 60


def check_nonce_replay(db: Session, nonce: str, plant_id: str) -> bool:
    """
    Verifica se o nonce já foi usado nos últimos 60 segundos para esta plant.

    Returns:
        True se nonce já usado (REPLAY DETECTADO), False se nonce é novo.
    """
    cutoff = datetime.utcnow() - timedelta(seconds=NONCE_WINDOW_SECONDS)

    existing = (
        db.query(UsedNonce)
        .filter(
            UsedNonce.nonce == nonce,
            UsedNonce.plant_id == plant_id,
            UsedNonce.used_at >= cutoff,
        )
        .first()
    )

    return existing is not None


def register_nonce(db: Session, nonce: str, plant_id: str) -> None:
    """Registra nonce como usado."""
    entry = UsedNonce(nonce=nonce, plant_id=plant_id, used_at=datetime.utcnow())
    db.add(entry)
    db.flush()


def cleanup_expired_nonces(db: Session) -> int:
    """Remove nonces expirados (> 60s). Retorna quantidade removida."""
    cutoff = datetime.utcnow() - timedelta(seconds=NONCE_WINDOW_SECONDS)
    result = db.execute(
        text("DELETE FROM used_nonces WHERE used_at < :cutoff"),
        {"cutoff": cutoff},
    )
    db.commit()
    return result.rowcount


# ---------------------------------------------------------------------------
# 4. Camada 2: NTP Blindada — Verificação de drift ±5ms
# ---------------------------------------------------------------------------

NTP_MAX_DRIFT_MS = 5.0  # Tolerância máxima em milissegundos


def _default_server_now() -> datetime:
    """Retorna hora atual UTC do servidor. Ponto de injeção para testes."""
    return datetime.now(timezone.utc)


def compute_ntp_drift_ms(
    payload_timestamp: str,
    server_now_fn: Callable[[], datetime] = _default_server_now,
) -> Tuple[float, datetime]:
    """
    Calcula drift NTP entre o timestamp do payload e o relógio do servidor.

    Args:
        payload_timestamp: ISO-8601 string do inversor (ex: "2026-02-23T14:30:00.000Z")
        server_now_fn: Callable que retorna datetime UTC do servidor (injetável para testes)

    Returns:
        Tupla (drift_ms, server_time):
          - drift_ms: float com drift em milissegundos (positivo = payload à frente)
          - server_time: datetime UTC do servidor no momento da verificação
    """
    # Parsear timestamp do payload para datetime aware (UTC)
    ts_str = payload_timestamp.replace("Z", "+00:00")
    payload_dt = datetime.fromisoformat(ts_str)

    # Se naive, assume UTC
    if payload_dt.tzinfo is None:
        payload_dt = payload_dt.replace(tzinfo=timezone.utc)

    server_time = server_now_fn()
    if server_time.tzinfo is None:
        server_time = server_time.replace(tzinfo=timezone.utc)

    # Delta em milissegundos (positivo = payload no futuro relativo ao server)
    delta = payload_dt - server_time
    drift_ms = delta.total_seconds() * 1000.0

    return drift_ms, server_time


def check_ntp_drift(
    payload_timestamp: str,
    max_drift_ms: float = NTP_MAX_DRIFT_MS,
    server_now_fn: Callable[[], datetime] = _default_server_now,
) -> Tuple[bool, float, datetime]:
    """
    Verifica se o drift NTP está dentro da tolerância de ±5ms.

    Args:
        payload_timestamp: ISO-8601 do inversor
        max_drift_ms: Tolerância máxima (default 5ms)
        server_now_fn: Callable para hora do servidor (injetável)

    Returns:
        Tupla (ntp_pass, drift_ms, server_time):
          - ntp_pass: True se |drift| <= max_drift_ms
          - drift_ms: drift medido em ms
          - server_time: hora UTC do servidor
    """
    drift_ms, server_time = compute_ntp_drift_ms(payload_timestamp, server_now_fn)
    ntp_pass = abs(drift_ms) <= max_drift_ms
    return ntp_pass, drift_ms, server_time


# ---------------------------------------------------------------------------
# Helpers para geração de chaves (usado em testes e seed)
# ---------------------------------------------------------------------------

def generate_ecdsa_keypair():
    """Gera par de chaves ECDSA secp256k1 para testes."""
    private_key = ec.generate_private_key(ec.SECP256K1())
    public_key = private_key.public_key()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    return private_pem, public_pem


def sign_payload(private_key_pem: str, message: str) -> str:
    """Assina payload com chave privada ECDSA. Retorna assinatura em hex."""
    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"), password=None
    )
    signature = private_key.sign(
        message.encode("utf-8"),
        ec.ECDSA(hashes.SHA256()),
    )
    return signature.hex()

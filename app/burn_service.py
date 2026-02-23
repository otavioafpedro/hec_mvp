"""
Serviço Burn — Queima irreversível de HECs com certificado de aposentadoria.

A queima (burn) é o ato de aposentar certificados de energia renovável,
comprovando que a energia foi efetivamente compensada/utilizada.

IRREVERSÍVEL — uma vez queimado, o HEC não pode ser restaurado.

Pipeline:
  1. Valida que o usuário possui saldo suficiente (hec_balance >= quantity)
  2. Seleciona HECs sold do usuário (via transactions → lot → certificates)
  3. Gera Burn Certificate JSON canônico
  4. Calcula SHA-256 do JSON
  5. Gera PDF do Burn Certificate
  6. Upload JSON + PDF para IPFS → CIDs
  7. Registro on-chain (hash + CID) → tx_hash
  8. Debita wallet (hec_balance, energy_balance_kwh)
  9. Marca HECs como "retired"
  10. Persiste BurnCertificate no banco
  11. COMMIT (ou ROLLBACK total)

Campos do Burn Certificate:
  - burn_id, user (email, name)
  - quantity, energy_kwh
  - burned_hec_ids com detalhes (hash, lot, ipfs_cid)
  - reason (offset | retirement | voluntary)
  - burned_at (UTC ISO 8601)
  - hash_sha256, ipfs_cid, registry_tx_hash
"""
import hashlib
import io
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.models import (
    User, Wallet, HECCertificate, Transaction, BurnCertificate,
)


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------

@dataclass
class BurnResult:
    """Resultado de um burn."""
    burn_id: uuid.UUID
    user_id: uuid.UUID
    quantity: int
    energy_kwh: float
    certificate_hash: str
    certificate_json: dict
    pdf_bytes: bytes
    ipfs_json_cid: Optional[str]
    ipfs_pdf_cid: Optional[str]
    ipfs_provider: Optional[str]
    registry_tx_hash: Optional[str]
    registry_block: Optional[int]
    contract_address: Optional[str]
    burned_hec_ids: List[str]
    reason: str
    burned_at: datetime
    status: str  # "burned"
    wallet_hec_after: int
    wallet_energy_after: float


# ---------------------------------------------------------------------------
# Build Burn Certificate JSON
# ---------------------------------------------------------------------------

def build_burn_certificate_json(
    burn_id: uuid.UUID,
    user: User,
    burned_hecs: List[HECCertificate],
    reason: str,
    burned_at: datetime,
) -> dict:
    """
    Monta JSON canônico do Burn Certificate.
    """
    total_energy = sum(float(h.energy_kwh) for h in burned_hecs)

    hec_details = []
    for h in burned_hecs:
        hec_details.append({
            "hec_id": str(h.hec_id),
            "energy_kwh": float(h.energy_kwh),
            "certificate_hash": h.hash_sha256,
            "lot_id": str(h.lot_id) if h.lot_id else None,
            "ipfs_json_cid": h.ipfs_json_cid,
            "registry_tx_hash": h.registry_tx_hash,
        })

    return {
        "burn_certificate": {
            "burn_id": str(burn_id),
            "version": "1.0",
            "standard": "HEC-BURN-CERT-BR-2026",
            "type": "BURN",
        },
        "user": {
            "email": user.email,
            "name": user.name,
        },
        "burn": {
            "quantity": len(burned_hecs),
            "total_energy_kwh": round(total_energy, 4),
            "reason": reason,
            "burned_at": burned_at.isoformat(),
            "irreversible": True,
        },
        "certificates_burned": hec_details,
        "metadata": {
            "ecosystem": "Solar One HUB / ABSOLAR",
            "chain": "polygon",
            "generated_by": "SENTINEL-AGIS-2.0",
        },
    }


# ---------------------------------------------------------------------------
# Compute hash
# ---------------------------------------------------------------------------

def compute_burn_hash(certificate_json: dict) -> str:
    """SHA-256 do JSON canônico do burn certificate."""
    canonical = json.dumps(
        certificate_json, sort_keys=True,
        separators=(",", ":"), ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Generate PDF
# ---------------------------------------------------------------------------

def generate_burn_certificate_pdf(cert_json: dict, cert_hash: str) -> bytes:
    """Gera PDF visual do Burn Certificate usando reportlab."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.colors import HexColor
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    w, h = A4
    c = canvas.Canvas(buf, pagesize=A4)

    burn = cert_json["burn_certificate"]
    user = cert_json["user"]
    burn_data = cert_json["burn"]
    hecs = cert_json["certificates_burned"]

    # Colors
    dark = HexColor("#111827")
    red = HexColor("#dc2626")
    gray = HexColor("#6b7280")
    light_bg = HexColor("#fef2f2")
    red_accent = HexColor("#991b1b")

    # ── Header ──
    c.setFillColor(red)
    c.rect(0, h - 80, w, 80, fill=True, stroke=False)

    c.setFillColor(HexColor("#ffffff"))
    c.setFont("Helvetica-Bold", 22)
    c.drawString(30, h - 40, "BURN CERTIFICATE")
    c.setFont("Helvetica", 11)
    c.drawString(30, h - 58, "Certificado de Aposentadoria de Energia — IRREVERSÍVEL")

    c.drawRightString(w - 30, h - 40, f"BURN #{burn['burn_id'][:8].upper()}")
    c.setFont("Helvetica", 9)
    c.drawRightString(w - 30, h - 55, f"Data: {burn_data['burned_at'][:10]}")

    # ── Accent line ──
    c.setStrokeColor(red_accent)
    c.setLineWidth(3)
    c.line(0, h - 82, w, h - 82)

    y = h - 115

    # ── IRREVERSÍVEL banner ──
    c.setFillColor(light_bg)
    c.rect(25, y - 25, w - 50, 35, fill=True, stroke=False)
    c.setFillColor(red)
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(w / 2, y - 12, "⚠ BURN IRREVERSÍVEL — CERTIFICADOS APOSENTADOS ⚠")
    y -= 45

    # ── User ──
    c.setFillColor(dark)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(30, y, "TITULAR")
    y -= 22
    c.setFont("Helvetica", 10)
    c.setFillColor(HexColor("#374151"))
    c.drawString(40, y, f"Nome: {user['name']}")
    y -= 16
    c.drawString(40, y, f"Email: {user['email']}")
    y -= 25

    # ── Energia queimada ──
    c.setFillColor(light_bg)
    c.rect(25, y - 55, w - 50, 65, fill=True, stroke=False)

    c.setFillColor(dark)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(30, y, "ENERGIA QUEIMADA")
    y -= 24

    c.setFont("Helvetica-Bold", 28)
    c.setFillColor(red)
    c.drawString(40, y, f"{burn_data['total_energy_kwh']:.4f} kWh")
    y -= 20

    c.setFont("Helvetica", 10)
    c.setFillColor(gray)
    c.drawString(40, y, f"{burn_data['quantity']} certificado(s) — Motivo: {burn_data['reason']}")
    y -= 30

    # ── HECs burned ──
    c.setFillColor(dark)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(30, y, "CERTIFICADOS QUEIMADOS")
    y -= 20

    c.setFont("Courier", 7)
    c.setFillColor(HexColor("#374151"))
    for i, hec in enumerate(hecs[:8]):  # Max 8 in PDF
        c.drawString(40, y,
            f"#{i+1} HEC {hec['hec_id'][:8]}  |  "
            f"{hec['energy_kwh']:.4f} kWh  |  "
            f"SHA: {hec['certificate_hash'][:16]}...")
        y -= 12

    if len(hecs) > 8:
        c.drawString(40, y, f"... +{len(hecs) - 8} certificado(s) adicionais")
        y -= 12

    y -= 15

    # ── Hash ──
    c.setFillColor(dark)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(30, y, "INTEGRIDADE DO BURN")
    y -= 18

    c.setFillColor(light_bg)
    c.rect(25, y - 14, w - 50, 26, fill=True, stroke=False)
    c.setFont("Courier", 7)
    c.setFillColor(dark)
    c.drawString(35, y - 5, f"SHA-256: {cert_hash}")
    y -= 35

    # ── Footer ──
    c.setStrokeColor(gray)
    c.setLineWidth(0.5)
    c.line(30, 55, w - 30, 55)

    c.setFont("Helvetica", 7)
    c.setFillColor(gray)
    c.drawString(30, 42, f"Solar One HUB / ABSOLAR — {burn['standard']}")
    c.drawRightString(w - 30, 42, f"BURN v{burn['version']} — {burn['burn_id']}")
    c.drawCentredString(w / 2, 28,
        "Este documento certifica a aposentadoria irreversível dos certificados de energia renovável listados acima.")

    c.save()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Execute Burn
# ---------------------------------------------------------------------------

def execute_burn(
    db: Session,
    user: User,
    quantity: int,
    reason: str = "voluntary",
    burned_at: Optional[datetime] = None,
) -> BurnResult:
    """
    Executa burn irreversível de HECs.

    Pipeline atômico:
      1. Valida saldo hec_balance >= quantity
      2. Seleciona HECs "sold" do usuário (via transactions)
      3. Gera Burn Certificate JSON + SHA-256 + PDF
      4. Upload IPFS
      5. Registro on-chain
      6. Debita wallet
      7. Marca HECs como "retired"
      8. Persiste BurnCertificate

    IRREVERSÍVEL — não há rollback após COMMIT.

    Args:
        db: Sessão do banco
        user: Usuário que está queimando
        quantity: Quantidade de HECs a queimar
        reason: Motivo (offset | retirement | voluntary)
        burned_at: Timestamp (default: now UTC)

    Returns:
        BurnResult

    Raises:
        ValueError: Saldo insuficiente, HECs insuficientes
    """
    if quantity <= 0:
        raise ValueError("Quantidade deve ser > 0")

    if reason not in ("offset", "retirement", "voluntary"):
        raise ValueError(
            f"Motivo inválido: {reason}. "
            f"Aceitos: offset, retirement, voluntary"
        )

    burned_at = burned_at or datetime.now(timezone.utc)

    # 1. Buscar wallet
    wallet = db.query(Wallet).filter(Wallet.user_id == user.user_id).first()
    if not wallet:
        raise ValueError(f"Wallet do usuário {user.user_id} não encontrada")

    if wallet.hec_balance < quantity:
        raise ValueError(
            f"Saldo HEC insuficiente — disponível: {wallet.hec_balance}, "
            f"solicitado: {quantity}"
        )

    # 2. Selecionar HECs do usuário (sold, via transactions)
    user_txs = (
        db.query(Transaction)
        .filter(
            Transaction.buyer_id == user.user_id,
            Transaction.status == "completed",
        )
        .all()
    )
    lot_ids = [tx.lot_id for tx in user_txs]

    if not lot_ids:
        raise ValueError("Nenhuma transação encontrada — não há HECs para queimar")

    # Find sold HECs in user's purchased lots
    available_hecs = (
        db.query(HECCertificate)
        .filter(
            HECCertificate.lot_id.in_(lot_ids),
            HECCertificate.status == "sold",
        )
        .limit(quantity)
        .all()
    )

    if len(available_hecs) < quantity:
        raise ValueError(
            f"Apenas {len(available_hecs)} HECs disponíveis para burn, "
            f"solicitado: {quantity}"
        )

    # 3. Build Burn Certificate JSON
    burn_id = uuid.uuid4()
    cert_json = build_burn_certificate_json(
        burn_id, user, available_hecs, reason, burned_at,
    )

    # 4. Compute SHA-256
    cert_hash = compute_burn_hash(cert_json)

    # 5. Generate PDF
    pdf_bytes = generate_burn_certificate_pdf(cert_json, cert_hash)

    # 6. Upload IPFS
    from app.ipfs_service import upload_certificate_to_ipfs
    ipfs_result = upload_certificate_to_ipfs(
        certificate_json=cert_json,
        pdf_bytes=pdf_bytes,
        hec_id=str(burn_id),
    )

    # 7. Register on-chain
    from app.blockchain import register_on_chain
    chain_result = register_on_chain(
        certificate_hash_hex=cert_hash,
        ipfs_cid=ipfs_result.json_cid,
    )

    # ════════════════════════════════════════════════════════════
    # TRANSAÇÃO ATÔMICA IRREVERSÍVEL
    # ════════════════════════════════════════════════════════════

    total_energy = sum(float(h.energy_kwh) for h in available_hecs)

    # 8a. Debitar wallet
    wallet.hec_balance -= quantity
    wallet.energy_balance_kwh -= Decimal(str(total_energy))

    # 8b. Marcar HECs como retired (irreversível)
    for hec in available_hecs:
        hec.status = "retired"

    # 8c. Persistir BurnCertificate
    burned_hec_id_list = [str(h.hec_id) for h in available_hecs]

    burn_record = BurnCertificate(
        burn_id=burn_id,
        user_id=user.user_id,
        quantity=quantity,
        energy_kwh=Decimal(str(total_energy)),
        certificate_json=cert_json,
        hash_sha256=cert_hash,
        ipfs_json_cid=ipfs_result.json_cid,
        ipfs_pdf_cid=ipfs_result.pdf_cid,
        ipfs_provider=ipfs_result.provider,
        registry_tx_hash=chain_result.tx_hash,
        registry_block=chain_result.block_number,
        contract_address=chain_result.contract_address,
        chain=chain_result.chain,
        burned_hec_ids=burned_hec_id_list,
        status="burned",
        reason=reason,
        burned_at=burned_at,
    )
    db.add(burn_record)

    # Don't commit — caller controls transaction

    return BurnResult(
        burn_id=burn_id,
        user_id=user.user_id,
        quantity=quantity,
        energy_kwh=total_energy,
        certificate_hash=cert_hash,
        certificate_json=cert_json,
        pdf_bytes=pdf_bytes,
        ipfs_json_cid=ipfs_result.json_cid,
        ipfs_pdf_cid=ipfs_result.pdf_cid,
        ipfs_provider=ipfs_result.provider,
        registry_tx_hash=chain_result.tx_hash,
        registry_block=chain_result.block_number,
        contract_address=chain_result.contract_address,
        burned_hec_ids=burned_hec_id_list,
        reason=reason,
        burned_at=burned_at,
        status="burned",
        wallet_hec_after=wallet.hec_balance,
        wallet_energy_after=float(wallet.energy_balance_kwh),
    )

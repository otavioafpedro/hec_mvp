"""
Serviço Marketplace — Compra atômica de HECs em lotes.

Regras de negócio:
  1. Só lotes com status "open" + backing completo podem ser comprados
  2. quantity <= available_quantity (não pode exceder disponível)
  3. Comprador deve ter saldo suficiente na wallet
  4. Transação atômica: debita saldo, credita HECs, decrementa available
  5. Se available_quantity chega a 0 → lot.status = "sold"

Fluxo atômico (tudo ou nada):
  1. Valida lote (open, backed, available)
  2. Calcula preço total (quantity × avg_kwh × price_per_kwh)
  3. Valida saldo do comprador
  4. BEGIN TRANSACTION
     a. Debita wallet.balance_brl
     b. Credita wallet.hec_balance + energy_balance_kwh
     c. Decrementa lot.available_quantity
     d. Atualiza lot.status se esgotado
     e. Marca HECs comprados como "sold"
     f. Cria Transaction record
  5. COMMIT (ou ROLLBACK em qualquer falha)
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.models import (
    HECCertificate, HECLot, User, Wallet, Transaction,
)


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------

@dataclass
class BuyResult:
    """Resultado de uma compra."""
    tx_id: uuid.UUID
    buyer_id: uuid.UUID
    lot_id: uuid.UUID
    quantity: int
    energy_kwh: float
    unit_price_brl: float
    total_price_brl: float
    wallet_balance_after: float
    wallet_hec_after: int
    wallet_energy_after: float
    lot_available_after: int
    lot_status_after: str
    status: str  # "completed"


# ---------------------------------------------------------------------------
# Buy HECs from lot
# ---------------------------------------------------------------------------

def buy_from_lot(
    db: Session,
    buyer_id: uuid.UUID,
    lot_id: uuid.UUID,
    quantity: int,
) -> BuyResult:
    """
    Compra atômica de HECs de um lote.

    Transação atômica:
      1. Valida lote (open, backed, available)
      2. Calcula preço total
      3. Valida saldo do comprador
      4. Debita wallet, credita HECs, decrementa lote
      5. Marca HECs como "sold"
      6. Cria Transaction record

    Args:
        db: Sessão do banco
        buyer_id: ID do comprador
        lot_id: ID do lote
        quantity: Quantidade de HECs a comprar

    Returns:
        BuyResult

    Raises:
        ValueError: Validações de negócio
    """
    if quantity <= 0:
        raise ValueError("Quantidade deve ser > 0")

    # 1. Buscar e validar lote
    lot = db.query(HECLot).filter(HECLot.lot_id == lot_id).first()
    if not lot:
        raise ValueError(f"Lote {lot_id} não encontrado")

    if lot.status not in ("open",):
        raise ValueError(
            f"Lote {lot_id} não está aberto para venda "
            f"(status: {lot.status})"
        )

    if not lot.price_per_kwh or lot.price_per_kwh <= 0:
        raise ValueError(
            f"Lote {lot_id} não possui preço definido — "
            f"defina price_per_kwh antes de vender"
        )

    # 2. Verificar available_quantity
    if quantity > lot.available_quantity:
        raise ValueError(
            f"Quantidade solicitada ({quantity}) excede disponível "
            f"({lot.available_quantity}) no lote {lot_id}"
        )

    # 3. Buscar comprador + wallet
    buyer = db.query(User).filter(User.user_id == buyer_id).first()
    if not buyer:
        raise ValueError(f"Comprador {buyer_id} não encontrado")
    if not buyer.is_active:
        raise ValueError("Conta do comprador desativada")

    wallet = db.query(Wallet).filter(Wallet.user_id == buyer_id).first()
    if not wallet:
        raise ValueError(f"Wallet do comprador {buyer_id} não encontrada")

    # 4. Selecionar HECs do lote (os primeiros `quantity` disponíveis)
    available_hecs = (
        db.query(HECCertificate)
        .filter(
            HECCertificate.lot_id == lot_id,
            HECCertificate.status == "listed",
        )
        .limit(quantity)
        .all()
    )

    if len(available_hecs) < quantity:
        raise ValueError(
            f"Apenas {len(available_hecs)} HECs disponíveis no lote, "
            f"solicitados: {quantity}"
        )

    # 5. Calcular preço
    total_energy = sum(float(h.energy_kwh) for h in available_hecs)
    unit_price = Decimal(str(lot.price_per_kwh))
    total_price = (unit_price * Decimal(str(total_energy))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    # 6. Validar saldo
    if wallet.balance_brl < total_price:
        raise ValueError(
            f"Saldo insuficiente — necessário: R$ {total_price}, "
            f"disponível: R$ {wallet.balance_brl}"
        )

    # ════════════════════════════════════════════════════════════
    # TRANSAÇÃO ATÔMICA — tudo ou nada
    # ════════════════════════════════════════════════════════════

    # a. Debitar wallet
    wallet.balance_brl -= total_price
    wallet.hec_balance += quantity
    wallet.energy_balance_kwh += Decimal(str(total_energy))

    # b. Decrementar lote
    lot.available_quantity -= quantity
    if lot.available_quantity == 0:
        lot.status = "sold"

    # c. Marcar HECs como sold
    for hec in available_hecs:
        hec.status = "sold"

    # d. Criar Transaction record
    tx_id = uuid.uuid4()
    tx = Transaction(
        tx_id=tx_id,
        buyer_id=buyer_id,
        lot_id=lot_id,
        quantity=quantity,
        energy_kwh=Decimal(str(total_energy)),
        unit_price_brl=unit_price,
        total_price_brl=total_price,
        status="completed",
    )
    db.add(tx)

    # Don't commit — caller controls transaction

    return BuyResult(
        tx_id=tx_id,
        buyer_id=buyer_id,
        lot_id=lot_id,
        quantity=quantity,
        energy_kwh=total_energy,
        unit_price_brl=float(unit_price),
        total_price_brl=float(total_price),
        wallet_balance_after=float(wallet.balance_brl),
        wallet_hec_after=wallet.hec_balance,
        wallet_energy_after=float(wallet.energy_balance_kwh),
        lot_available_after=lot.available_quantity,
        lot_status_after=lot.status,
        status="completed",
    )

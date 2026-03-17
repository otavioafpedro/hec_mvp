"""
Marketplace service for custody-backed HEC allocation.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import List

from sqlalchemy.orm import Session

from app.models.models import HECCertificate, HECLot, InventoryPosition, User, Wallet, Transaction


@dataclass
class BuyResult:
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
    status: str


def buy_from_lot(
    db: Session,
    buyer_id: uuid.UUID,
    lot_id: uuid.UUID,
    quantity: int,
) -> BuyResult:
    if quantity <= 0:
        raise ValueError("Quantity must be > 0")

    lot = db.query(HECLot).filter(HECLot.lot_id == lot_id).first()
    if not lot:
        raise ValueError(f"Lot {lot_id} not found")
    if lot.status != "open":
        raise ValueError(f"Lot {lot_id} is not open for sale (status: {lot.status})")
    if lot.inventory_status != "issued" or not lot.onchain_batch_token_id:
        raise ValueError(f"Lot {lot_id} does not have issued custody inventory")
    if lot.price_per_kwh is None or lot.price_per_kwh <= 0:
        raise ValueError(f"Lot {lot_id} has no valid price_per_kwh")
    if quantity > lot.available_quantity:
        raise ValueError(
            f"Requested quantity ({quantity}) exceeds available_quantity ({lot.available_quantity})"
        )

    buyer = db.query(User).filter(User.user_id == buyer_id).first()
    if not buyer:
        raise ValueError(f"Buyer {buyer_id} not found")
    if not buyer.is_active:
        raise ValueError("Buyer account is disabled")

    wallet = db.query(Wallet).filter(Wallet.user_id == buyer_id).first()
    if not wallet:
        raise ValueError(f"Wallet for buyer {buyer_id} not found")

    available_hecs: List[HECCertificate] = (
        db.query(HECCertificate)
        .filter(
            HECCertificate.lot_id == lot_id,
            HECCertificate.status == "custodied",
        )
        .order_by(HECCertificate.created_at.asc())
        .limit(quantity)
        .all()
    )
    if len(available_hecs) < quantity:
        raise ValueError(
            f"Only {len(available_hecs)} custodied HECs are available in lot {lot_id}"
        )

    total_energy = sum(float(hec.energy_kwh) for hec in available_hecs)
    unit_price = Decimal(str(lot.price_per_kwh))
    total_price = (unit_price * Decimal(str(total_energy))).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    if wallet.balance_brl < total_price:
        raise ValueError(
            f"Insufficient wallet balance - required: R$ {total_price}, available: R$ {wallet.balance_brl}"
        )

    wallet.balance_brl -= total_price
    wallet.hec_balance += quantity
    wallet.energy_balance_kwh += Decimal(str(total_energy))

    lot.available_quantity -= quantity
    if lot.available_quantity == 0:
        lot.status = "closed"

    source_hec_ids = [str(hec.hec_id) for hec in available_hecs]
    for hec in available_hecs:
        hec.status = "allocated"

    tx_id = uuid.uuid4()
    tx = Transaction(
        tx_id=tx_id,
        buyer_id=buyer_id,
        lot_id=lot_id,
        quantity=quantity,
        energy_kwh=Decimal(str(total_energy)),
        unit_price_brl=unit_price,
        total_price_brl=total_price,
        source_hec_ids=source_hec_ids,
        status="completed",
    )
    db.add(tx)

    position = InventoryPosition(
        position_id=uuid.uuid4(),
        wallet_id=wallet.wallet_id,
        user_id=buyer_id,
        lot_id=lot_id,
        transaction_id=tx_id,
        quantity=quantity,
        available_quantity=quantity,
        retired_quantity=0,
        energy_kwh_total=Decimal(str(total_energy)),
        energy_kwh_available=Decimal(str(total_energy)),
        source_hec_ids=source_hec_ids,
        status="active",
    )
    db.add(position)

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

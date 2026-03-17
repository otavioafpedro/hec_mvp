"""
Burn service for custody-backed retirement events and grouped receipts.
"""
import hashlib
import io
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional

from sqlalchemy.orm import Session

from app.blockchain import UNIT_SCALE, register_on_chain, retire_inventory_batch_on_chain
from app.ipfs_service import upload_certificate_to_ipfs
from app.models.models import BurnCertificate, HECCertificate, InventoryPosition, RetirementEvent, User, Wallet


@dataclass
class BurnResult:
    burn_id: uuid.UUID
    user_id: uuid.UUID
    quantity: int
    energy_kwh: float
    retired_mhec: int
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
    retirement_event_ids: List[str]
    receipt_token_ids: List[int]
    claimant_wallet: Optional[str]
    beneficiary_ref: Optional[str]
    beneficiary_ref_hash: Optional[str]
    external_operation_id: Optional[str]
    reason: str
    burned_at: datetime
    status: str
    wallet_hec_after: int
    wallet_energy_after: float


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _quantize_kwh(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def build_burn_certificate_json(
    burn_id: uuid.UUID,
    user: User,
    retirement_events_or_hecs,
    burned_hec_ids_or_reason=None,
    total_energy_kwh=None,
    quantity: Optional[int] = None,
    retired_mhec: Optional[int] = None,
    reason: Optional[str] = None,
    burned_at: Optional[datetime] = None,
    claimant_wallet: Optional[str] = None,
    beneficiary_ref: Optional[str] = None,
    beneficiary_ref_hash: Optional[str] = None,
    external_operation_id: Optional[str] = None,
) -> dict:
    legacy_mode = False
    records = list(retirement_events_or_hecs or [])
    if records and not isinstance(records[0], dict):
        legacy_mode = True

    if legacy_mode:
        reason = burned_hec_ids_or_reason if isinstance(burned_hec_ids_or_reason, str) else (reason or "voluntary")
        burned_at = total_energy_kwh if isinstance(total_energy_kwh, datetime) else (burned_at or datetime.now(timezone.utc))
        burned_hec_ids: List[str] = []
        total_energy = Decimal("0")
        certificates_burned = []
        retirement_events = []
        for hec in records:
            hec_id = str(getattr(hec, "hec_id", ""))
            energy = Decimal(str(getattr(hec, "energy_kwh", 0)))
            total_energy += energy
            burned_hec_ids.append(hec_id)
            certificates_burned.append(
                {
                    "hec_id": hec_id,
                    "energy_kwh": float(energy),
                    "certificate_hash": getattr(hec, "hash_sha256", None),
                    "lot_id": str(getattr(hec, "lot_id", "")) if getattr(hec, "lot_id", None) else None,
                    "ipfs_json_cid": getattr(hec, "ipfs_json_cid", None),
                    "registry_tx_hash": getattr(hec, "registry_tx_hash", None),
                }
            )
            retirement_events.append(
                {
                    "lot_id": str(getattr(hec, "lot_id", "")) if getattr(hec, "lot_id", None) else None,
                    "batch_token_id": None,
                    "amount_hec": 1,
                    "amount_mhec": UNIT_SCALE,
                    "energy_kwh": float(energy),
                    "claimant_wallet": claimant_wallet,
                    "beneficiary_ref_hash": beneficiary_ref_hash,
                    "external_operation_id": external_operation_id,
                    "protocol_operator": "platform_custody",
                    "onchain_retirement_id": None,
                    "receipt_token_id": None,
                    "receipt_contract_address": None,
                    "retirement_tx_hash": getattr(hec, "registry_tx_hash", None),
                    "retirement_block": None,
                    "source_hec_ids": [hec_id],
                    "retired_at": burned_at.isoformat(),
                }
            )
        total_energy_kwh = _quantize_kwh(total_energy)
        quantity = len(records)
        retired_mhec = quantity * UNIT_SCALE
    else:
        retirement_events = [dict(item) for item in records]
        burned_hec_ids = [str(item) for item in (burned_hec_ids_or_reason or [])]
        certificates_burned = [{"hec_id": hec_id} for hec_id in burned_hec_ids]
        total_energy_kwh = _quantize_kwh(Decimal(str(total_energy_kwh or 0)))
        quantity = int(quantity or len(burned_hec_ids))
        retired_mhec = int(retired_mhec or (quantity * UNIT_SCALE))
        reason = reason or "voluntary"
        burned_at = burned_at or datetime.now(timezone.utc)

    beneficiary_ref_hash = beneficiary_ref_hash or (_sha256_hex(beneficiary_ref) if beneficiary_ref else None)
    burn_section = {
        "quantity": quantity,
        "total_energy_kwh": float(total_energy_kwh),
        "reason": reason,
        "burned_at": burned_at.isoformat(),
        "irreversible": True,
    }
    retirement_section = {
        "quantity_hec": quantity,
        "quantity_mhec": retired_mhec,
        "total_energy_kwh": float(total_energy_kwh),
        "reason": reason,
        "burned_at": burned_at.isoformat(),
        "claimant_wallet": claimant_wallet,
        "beneficiary_ref_hash": beneficiary_ref_hash,
        "external_operation_id": external_operation_id,
        "irreversible": True,
    }
    cert_type = "BURN" if legacy_mode else "RETIREMENT"
    return {
        "burn_certificate": {
            "burn_id": str(burn_id),
            "version": "2.0",
            "standard": "HEC-RETIREMENT-CERT-BR-2026",
            "type": cert_type,
        },
        "user": {
            "email": user.email,
            "name": user.name,
        },
        "burn": burn_section,
        "retirement": retirement_section,
        "retirement_events": retirement_events,
        "certificates_burned": certificates_burned,
        "privacy": {
            "beneficiary_ref_present": beneficiary_ref is not None,
            "beneficiary_ref_hash": beneficiary_ref_hash,
        },
        "metadata": {
            "ecosystem": "Solar One HUB / ABSOLAR",
            "chain": "polygon-amoy",
            "generated_by": "HEC-CUSTODY-RETIREMENT-2.0",
        },
    }


def compute_burn_hash(certificate_json: dict) -> str:
    canonical = json.dumps(
        certificate_json,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def generate_burn_certificate_pdf(cert_json: dict, cert_hash: str) -> bytes:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    width, height = A4
    pdf = canvas.Canvas(buf, pagesize=A4)

    burn = cert_json["burn_certificate"]
    user = cert_json["user"]
    retirement = cert_json["retirement"]
    events = cert_json["retirement_events"]

    dark = HexColor("#111827")
    green = HexColor("#0f766e")
    soft = HexColor("#ecfeff")
    gray = HexColor("#6b7280")

    pdf.setFillColor(green)
    pdf.rect(0, height - 78, width, 78, fill=True, stroke=False)
    pdf.setFillColor(HexColor("#ffffff"))
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawString(30, height - 42, "RETIREMENT CERTIFICATE")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(30, height - 58, "Custodied inventory retirement receipt")
    pdf.drawRightString(width - 30, height - 42, f"BURN #{burn['burn_id'][:8].upper()}")

    y = height - 110
    pdf.setFillColor(dark)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(30, y, "Holder")
    y -= 18
    pdf.setFont("Helvetica", 10)
    pdf.drawString(40, y, f"Name: {user['name']}")
    y -= 14
    pdf.drawString(40, y, f"Email: {user['email']}")
    y -= 26

    pdf.setFillColor(soft)
    pdf.rect(25, y - 52, width - 50, 62, fill=True, stroke=False)
    pdf.setFillColor(dark)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(30, y, "Retirement Summary")
    y -= 22
    pdf.setFont("Helvetica-Bold", 24)
    pdf.setFillColor(green)
    pdf.drawString(40, y, f"{retirement['total_energy_kwh']:.4f} kWh")
    y -= 18
    pdf.setFont("Helvetica", 10)
    pdf.setFillColor(gray)
    pdf.drawString(
        40,
        y,
        f"{retirement['quantity_hec']} HEC | {retirement['quantity_mhec']} mHEC | reason: {retirement['reason']}",
    )
    y -= 26

    pdf.setFillColor(dark)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(30, y, "Retirement Events")
    y -= 18
    pdf.setFont("Courier", 7)
    for event in events[:10]:
        pdf.drawString(
            40,
            y,
            f"lot={event['lot_id'][:8]} batch={event['batch_token_id']} qty={event['amount_hec']} tx={event['retirement_tx_hash'][:12]}...",
        )
        y -= 11
        if y < 120:
            break

    y -= 10
    pdf.setFillColor(dark)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(30, y, "Integrity")
    y -= 16
    pdf.setFillColor(soft)
    pdf.rect(25, y - 12, width - 50, 24, fill=True, stroke=False)
    pdf.setFillColor(dark)
    pdf.setFont("Courier", 7)
    pdf.drawString(35, y - 4, f"SHA-256: {cert_hash}")

    pdf.setStrokeColor(gray)
    pdf.setLineWidth(0.5)
    pdf.line(30, 55, width - 30, 55)
    pdf.setFillColor(gray)
    pdf.setFont("Helvetica", 7)
    pdf.drawString(30, 40, "Solar One HUB / ABSOLAR")
    pdf.drawRightString(width - 30, 40, f"v{burn['version']}")

    pdf.save()
    return buf.getvalue()


def execute_burn(
    db: Session,
    user: User,
    quantity: int,
    reason: str = "voluntary",
    burned_at: Optional[datetime] = None,
    claimant_wallet: Optional[str] = None,
    beneficiary_ref: Optional[str] = None,
    external_operation_id: Optional[str] = None,
) -> BurnResult:
    if quantity <= 0:
        raise ValueError("Quantity must be > 0")
    if reason not in ("offset", "retirement", "voluntary"):
        raise ValueError("Invalid burn reason")

    burned_at = burned_at or datetime.now(timezone.utc)
    burn_id = uuid.uuid4()

    wallet = db.query(Wallet).filter(Wallet.user_id == user.user_id).first()
    if not wallet:
        raise ValueError(f"Wallet for user {user.user_id} not found")
    if wallet.hec_balance < quantity:
        raise ValueError(
            f"Insufficient HEC balance - available: {wallet.hec_balance}, requested: {quantity}"
        )

    positions: List[InventoryPosition] = (
        db.query(InventoryPosition)
        .filter(
            InventoryPosition.user_id == user.user_id,
            InventoryPosition.available_quantity > 0,
            InventoryPosition.status == "active",
        )
        .order_by(InventoryPosition.created_at.asc(), InventoryPosition.position_id.asc())
        .all()
    )
    available_hec_total = sum(int(position.available_quantity or 0) for position in positions)
    if available_hec_total < quantity:
        raise ValueError(
            f"Insufficient custody inventory - available: {available_hec_total}, requested: {quantity}"
        )

    claimant_wallet = claimant_wallet or wallet.wallet_address
    beneficiary_ref_hash = _sha256_hex(beneficiary_ref) if beneficiary_ref else None
    external_operation_id = external_operation_id or f"burn:{burn_id}"

    remaining = quantity
    total_energy = Decimal("0")
    burned_hec_ids: List[str] = []
    pending_events: List[dict] = []
    receipt_token_ids: List[int] = []

    for position in positions:
        if remaining == 0:
            break

        available_qty = int(position.available_quantity or 0)
        if available_qty <= 0:
            continue

        consume_qty = min(remaining, available_qty)
        if not position.lot or not position.lot.onchain_batch_token_id:
            raise ValueError(f"Lot {position.lot_id} does not have an issued on-chain batch")

        source_hec_ids = [str(item) for item in (position.source_hec_ids or [])]
        consumed_source_ids = source_hec_ids[:consume_qty]
        if len(consumed_source_ids) < consume_qty:
            raise ValueError(
                f"Inventory position {position.position_id} does not carry enough source HEC IDs"
            )
        remaining_source_ids = source_hec_ids[consume_qty:]

        available_energy = Decimal(str(position.energy_kwh_available))
        if consume_qty == available_qty:
            consumed_energy = available_energy
        else:
            energy_per_hec = available_energy / Decimal(available_qty)
            consumed_energy = _quantize_kwh(energy_per_hec * Decimal(consume_qty))

        retirement_reference = f"{external_operation_id}:{position.lot_id}:{len(pending_events) + 1}"
        chain_result = retire_inventory_batch_on_chain(
            batch_token_id=int(position.lot.onchain_batch_token_id),
            amount_units=consume_qty * UNIT_SCALE,
            claimant_wallet=claimant_wallet,
            retirement_reference=retirement_reference,
            beneficiary_ref_hash=beneficiary_ref_hash,
            purpose=reason,
        )

        position.available_quantity -= consume_qty
        position.retired_quantity += consume_qty
        position.energy_kwh_available = _quantize_kwh(available_energy - consumed_energy)
        position.source_hec_ids = remaining_source_ids
        if position.available_quantity == 0:
            position.status = "exhausted"

        consumed_uuid_ids = [uuid.UUID(hec_id) for hec_id in consumed_source_ids]
        allocated_hecs = (
            db.query(HECCertificate)
            .filter(HECCertificate.hec_id.in_(consumed_uuid_ids))
            .all()
        )
        for hec in allocated_hecs:
            hec.status = "retired"

        event_id = uuid.uuid4()
        pending_events.append(
            {
                "retirement_event_id": event_id,
                "lot_id": str(position.lot_id),
                "batch_token_id": int(position.lot.onchain_batch_token_id),
                "amount_hec": consume_qty,
                "amount_mhec": consume_qty * UNIT_SCALE,
                "energy_kwh": float(consumed_energy),
                "claimant_wallet": claimant_wallet,
                "beneficiary_ref_hash": beneficiary_ref_hash,
                "external_operation_id": external_operation_id,
                "protocol_operator": position.lot.custody_mode or "platform_custody",
                "onchain_retirement_id": chain_result.retirement_id,
                "receipt_token_id": chain_result.receipt_token_id,
                "receipt_contract_address": chain_result.contract_address,
                "retirement_tx_hash": chain_result.tx_hash,
                "retirement_block": chain_result.block_number,
                "source_hec_ids": consumed_source_ids,
                "retired_at": burned_at,
            }
        )
        receipt_token_ids.append(chain_result.receipt_token_id)
        burned_hec_ids.extend(consumed_source_ids)
        total_energy += consumed_energy
        remaining -= consume_qty

    if remaining != 0:
        raise ValueError("Retirement allocation failed to consume requested quantity")

    retired_mhec = quantity * UNIT_SCALE
    cert_json = build_burn_certificate_json(
        burn_id=burn_id,
        user=user,
        retirement_events=pending_events,
        burned_hec_ids=burned_hec_ids,
        total_energy_kwh=_quantize_kwh(total_energy),
        quantity=quantity,
        retired_mhec=retired_mhec,
        reason=reason,
        burned_at=burned_at,
        claimant_wallet=claimant_wallet,
        beneficiary_ref=beneficiary_ref,
        beneficiary_ref_hash=beneficiary_ref_hash,
        external_operation_id=external_operation_id,
    )
    cert_hash = compute_burn_hash(cert_json)
    pdf_bytes = generate_burn_certificate_pdf(cert_json, cert_hash)
    ipfs_result = upload_certificate_to_ipfs(
        certificate_json=cert_json,
        pdf_bytes=pdf_bytes,
        hec_id=str(burn_id),
    )
    chain_result = register_on_chain(
        certificate_hash_hex=cert_hash,
        ipfs_cid=ipfs_result.json_cid,
    )

    wallet.hec_balance -= quantity
    wallet.energy_balance_kwh -= _quantize_kwh(total_energy)

    burn_record = BurnCertificate(
        burn_id=burn_id,
        user_id=user.user_id,
        quantity=quantity,
        energy_kwh=_quantize_kwh(total_energy),
        retired_mhec=retired_mhec,
        certificate_json=cert_json,
        hash_sha256=cert_hash,
        ipfs_json_cid=ipfs_result.json_cid,
        ipfs_pdf_cid=ipfs_result.pdf_cid,
        ipfs_provider=ipfs_result.provider,
        registry_tx_hash=chain_result.tx_hash,
        registry_block=chain_result.block_number,
        contract_address=chain_result.contract_address,
        chain=chain_result.chain,
        claimant_wallet=claimant_wallet,
        beneficiary_ref=beneficiary_ref,
        beneficiary_ref_hash=beneficiary_ref_hash,
        external_operation_id=external_operation_id,
        burned_hec_ids=burned_hec_ids,
        retirement_event_ids=[str(item["retirement_event_id"]) for item in pending_events],
        receipt_token_ids=receipt_token_ids,
        status="burned",
        reason=reason,
        burned_at=burned_at,
    )
    db.add(burn_record)

    for item in pending_events:
        retirement_event = RetirementEvent(
            retirement_event_id=item["retirement_event_id"],
            burn_id=burn_id,
            user_id=user.user_id,
            lot_id=uuid.UUID(item["lot_id"]),
            batch_token_id=item["batch_token_id"],
            amount_hec=item["amount_hec"],
            amount_mhec=item["amount_mhec"],
            claimant_wallet=item["claimant_wallet"],
            beneficiary_ref=beneficiary_ref,
            beneficiary_ref_hash=item["beneficiary_ref_hash"],
            external_operation_id=item["external_operation_id"],
            protocol_operator=item["protocol_operator"],
            onchain_retirement_id=item["onchain_retirement_id"],
            receipt_token_id=item["receipt_token_id"],
            receipt_contract_address=item["receipt_contract_address"],
            retirement_tx_hash=item["retirement_tx_hash"],
            retirement_block=item["retirement_block"],
            source_hec_ids=item["source_hec_ids"],
            status="retired",
            retired_at=burned_at,
        )
        db.add(retirement_event)

    return BurnResult(
        burn_id=burn_id,
        user_id=user.user_id,
        quantity=quantity,
        energy_kwh=float(_quantize_kwh(total_energy)),
        retired_mhec=retired_mhec,
        certificate_hash=cert_hash,
        certificate_json=cert_json,
        pdf_bytes=pdf_bytes,
        ipfs_json_cid=ipfs_result.json_cid,
        ipfs_pdf_cid=ipfs_result.pdf_cid,
        ipfs_provider=ipfs_result.provider,
        registry_tx_hash=chain_result.tx_hash,
        registry_block=chain_result.block_number,
        contract_address=chain_result.contract_address,
        burned_hec_ids=burned_hec_ids,
        retirement_event_ids=[str(item["retirement_event_id"]) for item in pending_events],
        receipt_token_ids=receipt_token_ids,
        claimant_wallet=claimant_wallet,
        beneficiary_ref=beneficiary_ref,
        external_operation_id=external_operation_id,
        reason=reason,
        burned_at=burned_at,
        status="burned",
        wallet_hec_after=wallet.hec_balance,
        wallet_energy_after=float(wallet.energy_balance_kwh),
    )

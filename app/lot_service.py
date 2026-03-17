"""
Lot service for platform custody inventory issuance.
"""
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from sqlalchemy.orm import Session

from app.blockchain import UNIT_SCALE, issue_inventory_batch_on_chain
from app.ipfs_service import upload_json_document_to_ipfs
from app.models.models import HECCertificate, HECLot


@dataclass
class LotCreationResult:
    lot_id: uuid.UUID
    name: str
    description: Optional[str]
    total_quantity: int
    available_quantity: int
    total_energy_kwh: float
    certificate_count: int
    status: str
    custody_mode: str
    transferability_policy: str
    inventory_status: str
    batch_hash: str
    manifest_cid: Optional[str]
    onchain_batch_token_id: Optional[int]
    onchain_issued_tx_hash: Optional[str]
    hec_ids: List[str]
    created_at: datetime


def validate_hec_backing(hec: HECCertificate) -> Optional[str]:
    if hec.status not in ("registered", "minted"):
        return (
            f"HEC {hec.hec_id} - status={hec.status}, "
            f"required: registered or minted"
        )
    if not hec.ipfs_json_cid:
        return f"HEC {hec.hec_id} - missing ipfs_json_cid"
    if not hec.registry_tx_hash:
        return f"HEC {hec.hec_id} - missing registry_tx_hash"
    return None


def validate_hec_not_in_lot(hec: HECCertificate) -> Optional[str]:
    if hec.lot_id is not None:
        return f"HEC {hec.hec_id} already belongs to lot {hec.lot_id}"
    return None


def _canonical_json_hash(payload: dict) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _derive_period_bounds(hecs: List[HECCertificate]) -> tuple[int, int]:
    starts = []
    ends = []
    for hec in hecs:
        validation = getattr(hec, "validation", None)
        if validation and validation.period_start:
            starts.append(int(validation.period_start.replace(tzinfo=timezone.utc).timestamp()))
        if validation and validation.period_end:
            ends.append(int(validation.period_end.replace(tzinfo=timezone.utc).timestamp()))

    if not starts or not ends:
        now = int(datetime.now(timezone.utc).timestamp())
        return now, now

    return min(starts), max(ends)


def build_lot_manifest(
    lot_id: uuid.UUID,
    name: str,
    description: Optional[str],
    hecs: List[HECCertificate],
    price_per_kwh: Optional[float],
    period_start: int,
    period_end: int,
) -> dict:
    total_energy = round(sum(float(hec.energy_kwh) for hec in hecs), 4)
    return {
        "inventory_batch": {
            "lot_id": str(lot_id),
            "version": "2.0",
            "standard": "HEC-INVENTORY-CUSTODY-BR-2026",
            "custody_mode": "platform_custody",
            "transferability_policy": "non_transferable",
        },
        "lot": {
            "name": name,
            "description": description,
            "quantity": len(hecs),
            "total_energy_kwh": total_energy,
            "price_per_kwh_brl": price_per_kwh,
            "period_start": period_start,
            "period_end": period_end,
        },
        "certificates": [
            {
                "hec_id": str(hec.hec_id),
                "energy_kwh": float(hec.energy_kwh),
                "certificate_hash": hec.hash_sha256,
                "ipfs_json_cid": hec.ipfs_json_cid,
                "registry_tx_hash": hec.registry_tx_hash,
            }
            for hec in hecs
        ],
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": "1.0",
            "methodology_version": "HEC-CUSTODY-1.0",
        },
    }


def create_lot(
    db: Session,
    hec_ids: List[uuid.UUID],
    name: str,
    description: Optional[str] = None,
    price_per_kwh: Optional[float] = None,
) -> LotCreationResult:
    if not hec_ids:
        raise ValueError("HEC ID list cannot be empty")

    seen = set()
    unique_ids = []
    for hid in hec_ids:
        if hid not in seen:
            seen.add(hid)
            unique_ids.append(hid)

    hecs: List[HECCertificate] = []
    for hid in unique_ids:
        hec = db.query(HECCertificate).filter(HECCertificate.hec_id == hid).first()
        if not hec:
            raise ValueError(f"HEC {hid} not found")
        hecs.append(hec)

    errors = []
    for hec in hecs:
        err = validate_hec_backing(hec)
        if err:
            errors.append(err)
        err = validate_hec_not_in_lot(hec)
        if err:
            errors.append(err)
    if errors:
        raise ValueError("Lot creation validation failed:\n" + "\n".join(f"- {err}" for err in errors))

    lot_id = uuid.uuid4()
    qty = len(hecs)
    total_energy = sum(float(hec.energy_kwh) for hec in hecs)
    period_start, period_end = _derive_period_bounds(hecs)
    manifest = build_lot_manifest(
        lot_id=lot_id,
        name=name,
        description=description,
        hecs=hecs,
        price_per_kwh=price_per_kwh,
        period_start=period_start,
        period_end=period_end,
    )
    batch_hash = _canonical_json_hash(manifest)
    manifest_upload = upload_json_document_to_ipfs(
        payload=manifest,
        document_id=str(lot_id),
        filename_prefix="hec-lot-manifest",
    )
    total_units = qty * UNIT_SCALE
    issuance = issue_inventory_batch_on_chain(
        batch_hash_hex=batch_hash,
        manifest_cid=manifest_upload.json_cid,
        period_start=period_start,
        period_end=period_end,
        total_units=total_units,
        methodology_version="HEC-CUSTODY-1.0",
        schema_version="1.0",
    )

    lot = HECLot(
        lot_id=lot_id,
        name=name,
        description=description,
        lot_manifest_json=manifest,
        lot_manifest_cid=manifest_upload.json_cid,
        batch_hash=batch_hash,
        onchain_batch_token_id=issuance.batch_token_id,
        onchain_total_units=total_units,
        onchain_issued_tx_hash=issuance.tx_hash,
        onchain_issued_block=issuance.block_number,
        custody_mode="platform_custody",
        transferability_policy="non_transferable",
        inventory_status="issued",
        total_energy_kwh=Decimal(str(total_energy)),
        total_quantity=qty,
        available_quantity=qty,
        certificate_count=qty,
        price_per_kwh=Decimal(str(price_per_kwh)) if price_per_kwh is not None else None,
        status="open",
    )
    db.add(lot)

    for hec in hecs:
        hec.lot_id = lot_id
        hec.status = "custodied"

    return LotCreationResult(
        lot_id=lot_id,
        name=name,
        description=description,
        total_quantity=qty,
        available_quantity=qty,
        total_energy_kwh=total_energy,
        certificate_count=qty,
        status="open",
        custody_mode="platform_custody",
        transferability_policy="non_transferable",
        inventory_status="issued",
        batch_hash=batch_hash,
        manifest_cid=manifest_upload.json_cid,
        onchain_batch_token_id=issuance.batch_token_id,
        onchain_issued_tx_hash=issuance.tx_hash,
        hec_ids=[str(hec.hec_id) for hec in hecs],
        created_at=datetime.now(timezone.utc),
    )

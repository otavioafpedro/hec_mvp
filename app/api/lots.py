"""
Lot API for custody inventory creation and lookup.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.lot_service import create_lot
from app.models.models import HECLot
from app.schemas.lot import LotCertificateSummary, LotCreateRequest, LotResponse

router = APIRouter(prefix="/lots", tags=["HEC Lots"])


def _build_lot_response(lot: HECLot, include_certs: bool = True, message: str = "") -> LotResponse:
    certs = None
    if include_certs and lot.certificates:
        certs = [
            LotCertificateSummary(
                hec_id=cert.hec_id,
                energy_kwh=float(cert.energy_kwh),
                certificate_hash=cert.hash_sha256,
                ipfs_json_cid=cert.ipfs_json_cid,
                registry_tx_hash=cert.registry_tx_hash,
                status=cert.status,
            )
            for cert in lot.certificates
        ]

    all_backed = bool(lot.onchain_issued_tx_hash) and all(
        cert.registry_tx_hash is not None and cert.ipfs_json_cid is not None
        for cert in (lot.certificates or [])
    )

    return LotResponse(
        lot_id=lot.lot_id,
        name=lot.name,
        description=lot.description,
        total_quantity=lot.total_quantity,
        available_quantity=lot.available_quantity,
        total_energy_kwh=float(lot.total_energy_kwh),
        price_per_kwh=float(lot.price_per_kwh) if lot.price_per_kwh is not None else None,
        status=lot.status,
        custody_mode=lot.custody_mode,
        transferability_policy=lot.transferability_policy,
        inventory_status=lot.inventory_status,
        batch_hash=lot.batch_hash,
        lot_manifest_cid=lot.lot_manifest_cid,
        onchain_batch_token_id=lot.onchain_batch_token_id,
        onchain_issued_tx_hash=lot.onchain_issued_tx_hash,
        certificates=certs,
        backing_complete=all_backed,
        created_at=lot.created_at.isoformat() + 'Z',
        message=message or (
            f"Lot {lot.status.upper()} | qty={lot.total_quantity} | "
            f"custody={lot.custody_mode} | inventory={lot.inventory_status}"
        ),
    )


@router.post('/create', response_model=LotResponse, status_code=status.HTTP_201_CREATED)
def create_lot_endpoint(req: LotCreateRequest, db: Session = Depends(get_db)):
    try:
        result = create_lot(
            db=db,
            hec_ids=req.hec_ids,
            name=req.name,
            description=req.description,
            price_per_kwh=req.price_per_kwh,
        )
        db.commit()
    except ValueError as exc:
        err = str(exc)
        if 'not found' in err:
            code = status.HTTP_404_NOT_FOUND
        elif 'already belongs' in err:
            code = status.HTTP_409_CONFLICT
        else:
            code = status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=err)

    lot = db.query(HECLot).filter(HECLot.lot_id == result.lot_id).first()
    return _build_lot_response(
        lot,
        message=(
            f"Custody lot created with {result.total_quantity} HEC, "
            f"batch token {result.onchain_batch_token_id}, manifest {result.manifest_cid}"
        ),
    )


@router.get('/{lot_id}', response_model=LotResponse)
def get_lot(lot_id: UUID, db: Session = Depends(get_db)):
    lot = db.query(HECLot).filter(HECLot.lot_id == lot_id).first()
    if not lot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Lot {lot_id} not found")
    return _build_lot_response(lot)


@router.get('', response_model=list[LotResponse])
def list_lots(status_filter: str = None, db: Session = Depends(get_db)):
    query = db.query(HECLot)
    if status_filter:
        query = query.filter(HECLot.status == status_filter)
    lots = query.order_by(HECLot.created_at.desc()).all()
    return [_build_lot_response(lot, include_certs=False) for lot in lots]

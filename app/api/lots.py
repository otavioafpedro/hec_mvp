"""
Endpoints Lots — Criação e consulta de lotes HEC backed.

POST /lots/create    — Criar lote com HECs backed (backing completo obrigatório)
GET  /lots/{lot_id}  — Consultar lote por ID
GET  /lots           — Listar todos os lotes
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.models import HECCertificate, HECLot
from app.schemas.lot import LotCreateRequest, LotResponse, LotCertificateSummary
from app.lot_service import create_lot

router = APIRouter(prefix="/lots", tags=["HEC Lots"])


def _build_lot_response(lot: HECLot, include_certs: bool = True, message: str = "") -> LotResponse:
    """Build consistent LotResponse from DB record."""
    certs = None
    if include_certs and lot.certificates:
        certs = [
            LotCertificateSummary(
                hec_id=c.hec_id,
                energy_kwh=float(c.energy_kwh),
                certificate_hash=c.hash_sha256,
                ipfs_json_cid=c.ipfs_json_cid,
                registry_tx_hash=c.registry_tx_hash,
                status=c.status,
            )
            for c in lot.certificates
        ]

    all_backed = all(
        c.registry_tx_hash is not None and c.ipfs_json_cid is not None
        for c in (lot.certificates or [])
    )

    return LotResponse(
        lot_id=lot.lot_id,
        name=lot.name,
        description=lot.description,
        total_quantity=lot.total_quantity,
        available_quantity=lot.available_quantity,
        total_energy_kwh=float(lot.total_energy_kwh),
        price_per_kwh=float(lot.price_per_kwh) if lot.price_per_kwh else None,
        status=lot.status,
        certificates=certs,
        backing_complete=all_backed,
        created_at=lot.created_at.isoformat() + "Z",
        message=message or f"Lote {lot.status.upper()} — {lot.total_quantity} HECs, {float(lot.total_energy_kwh):.4f} kWh",
    )


@router.post(
    "/create",
    response_model=LotResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Criar lote de HECs backed",
    description=(
        "Cria um lote agrupando HECs com backing completo. "
        "Cada HEC DEVE ter: status=registered, ipfs_cid, registry_tx_hash. "
        "Bloqueia criação sem backing completo."
    ),
)
def create_lot_endpoint(req: LotCreateRequest, db: Session = Depends(get_db)):
    """
    Validações:
      1. Todos os HEC IDs devem existir
      2. Todos devem ter backing completo (registered + IPFS + on-chain)
      3. Nenhum pode estar em outro lote
      4. Lista não pode ser vazia
    """
    try:
        result = create_lot(
            db=db,
            hec_ids=req.hec_ids,
            name=req.name,
            description=req.description,
            price_per_kwh=req.price_per_kwh,
        )
        db.commit()
    except ValueError as e:
        err_msg = str(e)

        # Determine appropriate HTTP status
        if "não encontrado" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=err_msg,
            )
        elif "já pertence" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=err_msg,
            )
        elif "Backing incompleto" in err_msg or "vazia" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=err_msg,
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=err_msg,
            )

    # Fetch lot with certificates for response
    lot = db.query(HECLot).filter(HECLot.lot_id == result.lot_id).first()

    return _build_lot_response(
        lot,
        message=(
            f"Lote criado — {result.total_quantity} HECs, "
            f"{result.total_energy_kwh:.4f} kWh total, "
            f"backing completo ✓"
        ),
    )


@router.get(
    "/{lot_id}",
    response_model=LotResponse,
    summary="Consultar lote por ID",
)
def get_lot(lot_id: UUID, db: Session = Depends(get_db)):
    """Retorna dados do lote com resumo dos HECs."""
    lot = db.query(HECLot).filter(HECLot.lot_id == lot_id).first()

    if not lot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lote {lot_id} não encontrado",
        )

    return _build_lot_response(lot)


@router.get(
    "",
    response_model=list[LotResponse],
    summary="Listar todos os lotes",
)
def list_lots(
    status_filter: str = None,
    db: Session = Depends(get_db),
):
    """Lista todos os lotes, opcionalmente filtrado por status."""
    query = db.query(HECLot)
    if status_filter:
        query = query.filter(HECLot.status == status_filter)
    lots = query.order_by(HECLot.created_at.desc()).all()

    return [_build_lot_response(lot, include_certs=False) for lot in lots]

"""
Burn API — Queima irreversível de HECs com certificado.

POST /burn                  — Queimar HECs (irreversível)
GET  /burn/{burn_id}        — Consultar burn certificate
GET  /burn/{burn_id}/certificate — Download PDF do burn certificate
GET  /burns                 — Listar burns do usuário
"""
import io
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Header, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.models import User, BurnCertificate
from app.schemas.burn import (
    BurnRequest, BurnCertificateResponse, BurnListResponse, BurnVerifyResponse,
)
from app.burn_service import execute_burn, generate_burn_certificate_pdf
from app.auth import verify_token

router = APIRouter(tags=["Burn"])


# ---------------------------------------------------------------------------
# Auth dependency (same pattern as marketplace)
# ---------------------------------------------------------------------------

def get_current_user(
    authorization: str = Header(None, description="Token: Bearer <token>"),
    db: Session = Depends(get_db),
) -> User:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header obrigatório",
        )

    token = authorization
    if token.startswith("Bearer "):
        token = token[7:]

    payload = verify_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido ou expirado",
        )

    user = db.query(User).filter(User.user_id == payload.get("user_id")).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário não encontrado",
        )
    return user


# ---------------------------------------------------------------------------
# GET /burn/verify/{burn_id} — public verification for sustainability reports
# ---------------------------------------------------------------------------

@router.get(
    "/burn/verify/{burn_id}",
    response_model=BurnVerifyResponse,
    summary="Verificar claim/burn por ID (público)",
)
def verify_burn_public(
    burn_id: UUID,
    db: Session = Depends(get_db),
):
    burn = db.query(BurnCertificate).filter(
        BurnCertificate.burn_id == burn_id,
    ).first()

    if not burn:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Burn {burn_id} não encontrado",
        )

    backing_complete = burn.registry_tx_hash is not None
    return BurnVerifyResponse(
        burn_id=burn.burn_id,
        quantity=burn.quantity,
        energy_kwh=float(burn.energy_kwh),
        certificate_hash=burn.hash_sha256,
        ipfs_json_cid=burn.ipfs_json_cid,
        ipfs_pdf_cid=burn.ipfs_pdf_cid,
        ipfs_provider=burn.ipfs_provider,
        registry_tx_hash=burn.registry_tx_hash,
        registry_block=burn.registry_block,
        contract_address=burn.contract_address,
        chain=burn.chain,
        reason=burn.reason or "voluntary",
        status=burn.status,
        burned_at=burn.burned_at.isoformat() + "Z",
        backing_complete=backing_complete,
        message=(
            f"Claim/Burn verificado — {burn.quantity} HECs, "
            f"{float(burn.energy_kwh):.4f} kWh, "
            f"status: {burn.status.upper()}"
            + (" — backing completo" if backing_complete else "")
        ),
    )


# ---------------------------------------------------------------------------
# POST /burn
# ---------------------------------------------------------------------------

@router.post(
    "/burn",
    response_model=BurnCertificateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Queimar HECs (IRREVERSÍVEL)",
    description=(
        "Queima irreversível de HECs. Debita saldo, gera Burn Certificate "
        "(JSON + PDF), calcula SHA-256, upload IPFS, registro on-chain. "
        "Uma vez queimado, NÃO pode ser revertido."
    ),
)
def burn_hecs(
    req: BurnRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        result = execute_burn(
            db=db,
            user=user,
            quantity=req.quantity,
            reason=req.reason,
        )
        db.commit()
    except ValueError as e:
        err = str(e)
        if "insuficiente" in err:
            code = status.HTTP_422_UNPROCESSABLE_ENTITY
        elif "Motivo inválido" in err:
            code = status.HTTP_422_UNPROCESSABLE_ENTITY
        elif "não encontrada" in err:
            code = status.HTTP_404_NOT_FOUND
        else:
            code = status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=err)

    return BurnCertificateResponse(
        burn_id=result.burn_id,
        user_id=result.user_id,
        quantity=result.quantity,
        energy_kwh=result.energy_kwh,
        certificate_hash=result.certificate_hash,
        certificate_json=result.certificate_json,
        ipfs_json_cid=result.ipfs_json_cid,
        ipfs_pdf_cid=result.ipfs_pdf_cid,
        ipfs_provider=result.ipfs_provider,
        registry_tx_hash=result.registry_tx_hash,
        registry_block=result.registry_block,
        contract_address=result.contract_address,
        chain="polygon-amoy",
        burned_hec_ids=result.burned_hec_ids,
        reason=result.reason,
        status="burned",
        irreversible=True,
        burned_at=result.burned_at.isoformat() + "Z",
        wallet_hec_after=result.wallet_hec_after,
        wallet_energy_after=result.wallet_energy_after,
        message=(
            f"🔥 BURN IRREVERSÍVEL — {result.quantity} HECs, "
            f"{result.energy_kwh:.4f} kWh aposentados. "
            f"Hash: {result.certificate_hash[:16]}..., "
            f"IPFS: {result.ipfs_json_cid}, "
            f"tx: {result.registry_tx_hash[:16]}..."
        ),
    )


# ---------------------------------------------------------------------------
# GET /burn/{burn_id}
# ---------------------------------------------------------------------------

@router.get(
    "/burn/{burn_id}",
    response_model=BurnCertificateResponse,
    summary="Consultar burn certificate",
)
def get_burn(
    burn_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    burn = db.query(BurnCertificate).filter(
        BurnCertificate.burn_id == burn_id,
    ).first()

    if not burn:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Burn {burn_id} não encontrado",
        )

    # User can only see own burns
    if burn.user_id != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso negado — burn pertence a outro usuário",
        )

    return BurnCertificateResponse(
        burn_id=burn.burn_id,
        user_id=burn.user_id,
        quantity=burn.quantity,
        energy_kwh=float(burn.energy_kwh),
        certificate_hash=burn.hash_sha256,
        certificate_json=burn.certificate_json,
        ipfs_json_cid=burn.ipfs_json_cid,
        ipfs_pdf_cid=burn.ipfs_pdf_cid,
        ipfs_provider=burn.ipfs_provider,
        registry_tx_hash=burn.registry_tx_hash,
        registry_block=burn.registry_block,
        contract_address=burn.contract_address,
        chain=burn.chain,
        burned_hec_ids=burn.burned_hec_ids or [],
        reason=burn.reason or "voluntary",
        status=burn.status,
        irreversible=True,
        burned_at=burn.burned_at.isoformat() + "Z",
        message=f"Burn certificate — {burn.quantity} HECs, {float(burn.energy_kwh):.4f} kWh",
    )


# ---------------------------------------------------------------------------
# GET /burn/{burn_id}/certificate — PDF download
# ---------------------------------------------------------------------------

@router.get(
    "/burn/{burn_id}/certificate",
    summary="Download PDF do Burn Certificate",
    responses={200: {"content": {"application/pdf": {}}}},
)
def download_burn_certificate(
    burn_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    burn = db.query(BurnCertificate).filter(
        BurnCertificate.burn_id == burn_id,
    ).first()

    if not burn:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Burn {burn_id} não encontrado",
        )

    if burn.user_id != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso negado",
        )

    if not burn.certificate_json:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Burn certificate JSON não disponível",
        )

    pdf_bytes = generate_burn_certificate_pdf(
        burn.certificate_json, burn.hash_sha256,
    )

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="BURN-{str(burn.burn_id)[:8]}.pdf"'
            ),
        },
    )


# ---------------------------------------------------------------------------
# GET /burns — list user's burns
# ---------------------------------------------------------------------------

@router.get(
    "/burns",
    response_model=list[BurnListResponse],
    summary="Listar burns do usuário",
)
def list_burns(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    burns = (
        db.query(BurnCertificate)
        .filter(BurnCertificate.user_id == user.user_id)
        .order_by(BurnCertificate.burned_at.desc())
        .all()
    )

    return [
        BurnListResponse(
            burn_id=b.burn_id,
            quantity=b.quantity,
            energy_kwh=float(b.energy_kwh),
            certificate_hash=b.hash_sha256,
            reason=b.reason or "voluntary",
            status=b.status,
            registry_tx_hash=b.registry_tx_hash,
            burned_at=b.burned_at.isoformat() + "Z",
        )
        for b in burns
    ]

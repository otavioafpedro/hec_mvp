"""
Burn API for custody-backed retirement receipts.
"""
import io
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.auth import verify_token
from app.burn_service import execute_burn, generate_burn_certificate_pdf
from app.db.session import get_db
from app.models.models import BurnCertificate, User
from app.schemas.burn import BurnCertificateResponse, BurnListResponse, BurnRequest, BurnVerifyResponse

router = APIRouter(tags=["Burn"])


def get_current_user(
    authorization: str = Header(None, description="Token: Bearer <token>"),
    db: Session = Depends(get_db),
) -> User:
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization header is required")

    token = authorization[7:] if authorization.startswith("Bearer ") else authorization
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    user_id = payload.get("user_id")
    try:
        user_uuid = UUID(str(user_id))
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: malformed user_id")

    user = db.query(User).filter(User.user_id == user_uuid).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


@router.get('/burn/verify/{burn_id}', response_model=BurnVerifyResponse)
def verify_burn_public(burn_id: UUID, db: Session = Depends(get_db)):
    burn = db.query(BurnCertificate).filter(BurnCertificate.burn_id == burn_id).first()
    if not burn:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Burn {burn_id} not found")

    backing_complete = burn.registry_tx_hash is not None and burn.retirement_event_ids is not None
    return BurnVerifyResponse(
        burn_id=burn.burn_id,
        quantity=burn.quantity,
        energy_kwh=float(burn.energy_kwh),
        retired_mhec=int(burn.retired_mhec or 0),
        certificate_hash=burn.hash_sha256,
        ipfs_json_cid=burn.ipfs_json_cid,
        ipfs_pdf_cid=burn.ipfs_pdf_cid,
        ipfs_provider=burn.ipfs_provider,
        registry_tx_hash=burn.registry_tx_hash,
        registry_block=burn.registry_block,
        contract_address=burn.contract_address,
        chain=burn.chain,
        claimant_wallet=burn.claimant_wallet,
        beneficiary_ref_hash=burn.beneficiary_ref_hash,
        external_operation_id=burn.external_operation_id,
        reason=burn.reason or 'voluntary',
        status=burn.status,
        burned_at=burn.burned_at.isoformat() + 'Z',
        backing_complete=backing_complete,
        message=(
            f"Retirement verified: {burn.quantity} HEC, {float(burn.energy_kwh):.4f} kWh, "
            f"{int(burn.retired_mhec or 0)} mHEC"
        ),
    )


@router.post('/burn', response_model=BurnCertificateResponse, status_code=status.HTTP_201_CREATED)
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
            claimant_wallet=req.claimant_wallet,
            beneficiary_ref=req.beneficiary_ref,
            external_operation_id=req.external_operation_id,
        )
        db.commit()
    except ValueError as exc:
        err = str(exc)
        if 'not found' in err:
            code = status.HTTP_404_NOT_FOUND
        else:
            code = status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=err)

    return BurnCertificateResponse(
        burn_id=result.burn_id,
        user_id=result.user_id,
        quantity=result.quantity,
        energy_kwh=result.energy_kwh,
        retired_mhec=result.retired_mhec,
        certificate_hash=result.certificate_hash,
        certificate_json=result.certificate_json,
        ipfs_json_cid=result.ipfs_json_cid,
        ipfs_pdf_cid=result.ipfs_pdf_cid,
        ipfs_provider=result.ipfs_provider,
        registry_tx_hash=result.registry_tx_hash,
        registry_block=result.registry_block,
        contract_address=result.contract_address,
        chain='polygon-amoy',
        burned_hec_ids=result.burned_hec_ids,
        retirement_event_ids=result.retirement_event_ids,
        receipt_token_ids=result.receipt_token_ids,
        claimant_wallet=result.claimant_wallet,
        beneficiary_ref=result.beneficiary_ref,
        external_operation_id=result.external_operation_id,
        reason=result.reason,
        status=result.status,
        irreversible=True,
        burned_at=result.burned_at.isoformat() + 'Z',
        wallet_hec_after=result.wallet_hec_after,
        wallet_energy_after=result.wallet_energy_after,
        message=(
            f"Retirement completed: {result.quantity} HEC, {result.energy_kwh:.4f} kWh, "
            f"receipt hash {result.certificate_hash[:16]}..."
        ),
    )


@router.get('/burn/{burn_id}', response_model=BurnCertificateResponse)
def get_burn(
    burn_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    burn = db.query(BurnCertificate).filter(BurnCertificate.burn_id == burn_id).first()
    if not burn:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Burn {burn_id} not found")
    if burn.user_id != user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    return BurnCertificateResponse(
        burn_id=burn.burn_id,
        user_id=burn.user_id,
        quantity=burn.quantity,
        energy_kwh=float(burn.energy_kwh),
        retired_mhec=int(burn.retired_mhec or 0),
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
        retirement_event_ids=burn.retirement_event_ids or [],
        receipt_token_ids=burn.receipt_token_ids or [],
        claimant_wallet=burn.claimant_wallet,
        beneficiary_ref=burn.beneficiary_ref,
        beneficiary_ref_hash=burn.beneficiary_ref_hash,
        external_operation_id=burn.external_operation_id,
        reason=burn.reason or 'voluntary',
        status=burn.status,
        irreversible=True,
        burned_at=burn.burned_at.isoformat() + 'Z',
        message=f"Retirement receipt for {burn.quantity} HEC and {float(burn.energy_kwh):.4f} kWh",
    )


@router.get('/burn/{burn_id}/certificate', responses={200: {'content': {'application/pdf': {}}}})
def download_burn_certificate(
    burn_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    burn = db.query(BurnCertificate).filter(BurnCertificate.burn_id == burn_id).first()
    if not burn:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Burn {burn_id} not found")
    if burn.user_id != user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if not burn.certificate_json:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Burn certificate JSON is not available")

    pdf_bytes = generate_burn_certificate_pdf(burn.certificate_json, burn.hash_sha256)
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="RETIREMENT-{str(burn.burn_id)[:8]}.pdf"'},
    )


@router.get('/burns', response_model=list[BurnListResponse])
def list_burns(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    burns = (
        db.query(BurnCertificate)
        .filter(BurnCertificate.user_id == user.user_id)
        .order_by(BurnCertificate.burned_at.desc())
        .all()
    )
    return [
        BurnListResponse(
            burn_id=burn.burn_id,
            quantity=burn.quantity,
            energy_kwh=float(burn.energy_kwh),
            retired_mhec=int(burn.retired_mhec or 0),
            certificate_hash=burn.hash_sha256,
            reason=burn.reason or 'voluntary',
            status=burn.status,
            registry_tx_hash=burn.registry_tx_hash,
            burned_at=burn.burned_at.isoformat() + 'Z',
        )
        for burn in burns
    ]

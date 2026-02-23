"""
Endpoints HEC — Emissão, consulta, verificação e registro on-chain.

POST /hec/issue            — Emitir HEC (JSON+PDF+IPFS+on-chain) ← pipeline completo
POST /hec/register         — Registro manual on-chain de HEC pendente
GET  /hec/{hec_id}         — Consultar certificado por ID
GET  /hec/{hec_id}/pdf     — Download PDF do certificado
GET  /hec/verify/{hec_id}  — Verificar integridade via IPFS (hash 100%)
GET  /hec/onchain/{hec_id} — Verificar registro on-chain
"""
import io
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.models import Plant, Validation, HECCertificate
from app.schemas.hec import (
    HECIssueRequest, HECRegisterRequest, HECResponse,
    HECVerifyResponse, OnChainVerifyResponse,
)
from app.hec_generator import issue_hec, generate_certificate_pdf
from app.ipfs_service import verify_certificate_from_ipfs
from app.blockchain import register_on_chain, verify_on_chain

router = APIRouter(prefix="/hec", tags=["HEC Certificates"])


def _build_hec_response(hec, plant_id=None, message=None) -> HECResponse:
    """Helper to build consistent HECResponse from DB record."""
    pid = plant_id or (hec.validation.plant_id if hec.validation else None)
    backing = hec.registry_tx_hash is not None
    return HECResponse(
        hec_id=hec.hec_id,
        validation_id=hec.validation_id,
        plant_id=pid,
        energy_kwh=float(hec.energy_kwh),
        certificate_hash=hec.hash_sha256,
        status=hec.status,
        chain=hec.chain or "polygon",
        issued_at=hec.minted_at.isoformat() + "Z",
        certificate_json=hec.certificate_json,
        pdf_available=hec.certificate_json is not None,
        ipfs_json_cid=hec.ipfs_json_cid,
        ipfs_pdf_cid=hec.ipfs_pdf_cid,
        ipfs_provider=hec.ipfs_provider,
        registry_tx_hash=hec.registry_tx_hash,
        registry_block=hec.registry_block,
        contract_address=hec.contract_address,
        backing_complete=backing,
        message=message or f"HEC {hec.status.upper()}" + (" — backing completo" if backing else ""),
    )


@router.post(
    "/issue",
    response_model=HECResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Emitir certificado HEC (pipeline completo)",
    description=(
        "Emite HEC: JSON canônico → SHA-256 → PDF → IPFS upload → "
        "registro on-chain → persiste com status=registered."
    ),
)
def issue_certificate(req: HECIssueRequest, db: Session = Depends(get_db)):
    # 1. Buscar validação
    validation = db.query(Validation).filter(
        Validation.validation_id == req.validation_id
    ).first()

    if not validation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Validação {req.validation_id} não encontrada",
        )

    if validation.status != "approved":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Validação {req.validation_id} não é APPROVED "
                f"(status atual: {validation.status}). "
                f"Apenas validações APPROVED podem gerar HEC."
            ),
        )

    # 2. Verificar duplicata
    existing = db.query(HECCertificate).filter(
        HECCertificate.validation_id == req.validation_id
    ).first()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Já existe HEC ({existing.hec_id}) "
                f"para a validação {req.validation_id}"
            ),
        )

    # 3. Buscar planta
    plant = db.query(Plant).filter(
        Plant.plant_id == validation.plant_id
    ).first()

    if not plant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Planta {validation.plant_id} não encontrada",
        )

    # 4. Emitir (JSON → SHA-256 → PDF → IPFS → on-chain → persist)
    result = issue_hec(db, plant, validation)
    db.commit()

    backing = result.registry_tx_hash is not None
    return HECResponse(
        hec_id=result.hec_id,
        validation_id=result.validation_id,
        plant_id=result.plant_id,
        energy_kwh=result.energy_kwh,
        certificate_hash=result.certificate_hash,
        status=result.status,
        chain=result.contract_address and "polygon-amoy" or "polygon",
        issued_at=result.issued_at.isoformat() + "Z",
        certificate_json=result.certificate_json,
        pdf_available=True,
        ipfs_json_cid=result.ipfs_json_cid,
        ipfs_pdf_cid=result.ipfs_pdf_cid,
        ipfs_provider=result.ipfs_provider,
        registry_tx_hash=result.registry_tx_hash,
        registry_block=result.registry_block,
        contract_address=result.contract_address,
        backing_complete=backing,
        message=(
            f"HEC emitido — {result.energy_kwh:.4f} kWh, "
            f"hash: {result.certificate_hash[:16]}..., "
            f"IPFS: {result.ipfs_json_cid}, "
            f"tx: {result.registry_tx_hash[:16]}..., "
            f"status: {result.status.upper()}"
            + (" — BACKING COMPLETO" if backing else "")
        ),
    )


@router.post(
    "/register",
    response_model=HECResponse,
    summary="Registrar HEC pendente on-chain",
    description=(
        "Para HECs em status=pending que ainda não foram registrados on-chain. "
        "Registra hash + IPFS CID no contrato e atualiza status para registered."
    ),
)
def register_certificate(req: HECRegisterRequest, db: Session = Depends(get_db)):
    hec = db.query(HECCertificate).filter(
        HECCertificate.hec_id == req.hec_id
    ).first()

    if not hec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"HEC {req.hec_id} não encontrado",
        )

    if hec.registry_tx_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"HEC {req.hec_id} já registrado on-chain "
                f"(tx: {hec.registry_tx_hash})"
            ),
        )

    if not hec.ipfs_json_cid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"HEC {req.hec_id} não possui IPFS CID — upload IPFS necessário primeiro",
        )

    # Register on-chain
    chain_result = register_on_chain(
        certificate_hash_hex=hec.hash_sha256,
        ipfs_cid=hec.ipfs_json_cid,
    )

    # Update DB
    hec.registry_tx_hash = chain_result.tx_hash
    hec.registry_block = chain_result.block_number
    hec.contract_address = chain_result.contract_address
    hec.registered_at = chain_result.registered_at
    hec.status = "registered"
    hec.chain = chain_result.chain
    db.commit()

    return _build_hec_response(
        hec,
        message=(
            f"HEC registrado on-chain — "
            f"tx: {chain_result.tx_hash[:16]}..., "
            f"block: {chain_result.block_number}, "
            f"status: REGISTERED — BACKING COMPLETO"
        ),
    )


@router.get(
    "/verify/{hec_id}",
    response_model=HECVerifyResponse,
    summary="Verificar integridade via IPFS (hash 100%)",
)
def verify_certificate(hec_id: UUID, db: Session = Depends(get_db)):
    hec = db.query(HECCertificate).filter(
        HECCertificate.hec_id == hec_id
    ).first()

    if not hec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"HEC {hec_id} não encontrado",
        )

    if not hec.ipfs_json_cid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"HEC {hec_id} não possui CID IPFS",
        )

    result = verify_certificate_from_ipfs(
        hec_id=str(hec.hec_id),
        stored_hash=hec.hash_sha256,
        json_cid=hec.ipfs_json_cid,
        pdf_cid=hec.ipfs_pdf_cid,
    )

    return HECVerifyResponse(
        verified=result.verified,
        hec_id=hec.hec_id,
        stored_hash=result.stored_hash,
        recalculated_hash=result.recalculated_hash,
        match=result.match,
        json_cid=result.json_cid,
        pdf_cid=result.pdf_cid,
        json_size_bytes=result.json_size_bytes,
        ipfs_provider=result.ipfs_provider,
        verified_at=result.verified_at,
        certificate_json=result.certificate_json,
        reason=result.reason,
    )


@router.get(
    "/onchain/{hec_id}",
    response_model=OnChainVerifyResponse,
    summary="Verificar registro on-chain",
    description="Consulta o contrato HECRegistry para verificar se hash está registrado.",
)
def verify_onchain(hec_id: UUID, db: Session = Depends(get_db)):
    hec = db.query(HECCertificate).filter(
        HECCertificate.hec_id == hec_id
    ).first()

    if not hec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"HEC {hec_id} não encontrado",
        )

    result = verify_on_chain(hec.hash_sha256)

    return OnChainVerifyResponse(
        exists=result.exists,
        certificate_hash=result.certificate_hash,
        ipfs_cid=result.ipfs_cid,
        registered_at=result.registered_at,
        block_number=result.block_number,
        contract_address=result.contract_address,
        chain=result.chain,
        provider=result.provider,
        backing_complete=result.exists,
    )


@router.get(
    "/{hec_id}",
    response_model=HECResponse,
    summary="Consultar certificado HEC",
)
def get_certificate(hec_id: UUID, db: Session = Depends(get_db)):
    hec = db.query(HECCertificate).filter(
        HECCertificate.hec_id == hec_id
    ).first()

    if not hec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"HEC {hec_id} não encontrado",
        )

    return _build_hec_response(hec)


@router.get(
    "/{hec_id}/pdf",
    summary="Download PDF do certificado HEC",
    responses={200: {"content": {"application/pdf": {}}}},
)
def download_certificate_pdf(hec_id: UUID, db: Session = Depends(get_db)):
    hec = db.query(HECCertificate).filter(
        HECCertificate.hec_id == hec_id
    ).first()

    if not hec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"HEC {hec_id} não encontrado",
        )

    if not hec.certificate_json:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Certificado não possui JSON — PDF indisponível",
        )

    pdf_bytes = generate_certificate_pdf(hec.certificate_json, hec.hash_sha256)

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="HEC-{str(hec.hec_id)[:8]}.pdf"'
        },
    )

"""Pydantic schemas para certificados HEC."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class HECIssueRequest(BaseModel):
    """Request para emissão manual de HEC a partir de validation_id."""
    validation_id: UUID = Field(..., description="ID da validação APPROVED")


class HECRegisterRequest(BaseModel):
    """Request para registro manual on-chain de HEC já emitido."""
    hec_id: UUID = Field(..., description="ID do certificado HEC a registrar")


class HECResponse(BaseModel):
    """Resposta da emissão/consulta de certificado HEC."""
    hec_id: UUID
    validation_id: UUID
    plant_id: UUID
    energy_kwh: float
    certificate_hash: str = Field(..., description="SHA-256 do certificado canônico")
    status: str = Field("pending", description="pending | registered | minted | listed | sold | retired")
    chain: str = Field("polygon", description="Blockchain target")
    issued_at: str
    certificate_json: Optional[dict] = Field(None, description="JSON completo do certificado")
    pdf_available: bool = Field(False, description="True se PDF foi gerado")
    # IPFS
    ipfs_json_cid: Optional[str] = Field(None, description="CID do JSON no IPFS")
    ipfs_pdf_cid: Optional[str] = Field(None, description="CID do PDF no IPFS")
    ipfs_provider: Optional[str] = Field(None, description="Provider IPFS usado")
    # On-chain registry
    registry_tx_hash: Optional[str] = Field(None, description="Transaction hash do registro on-chain (0x...)")
    registry_block: Optional[int] = Field(None, description="Block number do registro")
    contract_address: Optional[str] = Field(None, description="Endereço do contrato HECRegistry")
    backing_complete: bool = Field(False, description="True se registry_tx_hash existir (lastro completo)")
    message: str = "HEC emitido com sucesso"


class HECVerifyResponse(BaseModel):
    """Resposta da verificação de integridade via IPFS."""
    verified: bool = Field(..., description="True se hash bate 100%")
    hec_id: UUID
    stored_hash: str = Field(..., description="SHA-256 armazenado no DB")
    recalculated_hash: str = Field(..., description="SHA-256 recalculado do IPFS")
    match: bool = Field(..., description="stored == recalculated")
    json_cid: Optional[str] = Field(None, description="CID do JSON usado")
    pdf_cid: Optional[str] = Field(None, description="CID do PDF")
    json_size_bytes: int = Field(0, description="Tamanho do JSON baixado")
    ipfs_provider: str = Field("", description="Provider IPFS")
    verified_at: str = Field(..., description="Timestamp da verificação")
    certificate_json: Optional[dict] = Field(None, description="JSON recuperado do IPFS")
    reason: str = Field("", description="Detalhes da verificação")


class OnChainVerifyResponse(BaseModel):
    """Resposta da verificação on-chain."""
    exists: bool = Field(..., description="True se hash registrado on-chain")
    certificate_hash: str
    ipfs_cid: str = Field("", description="CID armazenado no contrato")
    registered_at: int = Field(0, description="Timestamp Unix do bloco")
    block_number: int = Field(0, description="Block number do registro")
    contract_address: str = Field("", description="Endereço do contrato")
    chain: str = Field("", description="Chain consultada")
    provider: str = Field("", description="Provider usado")
    backing_complete: bool = Field(False, description="True se registrado on-chain")


class HECError(BaseModel):
    """Erro na emissão de HEC."""
    status: str = "error"
    error: str
    detail: Optional[str] = None

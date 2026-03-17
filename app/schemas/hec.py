"""Pydantic schemas for HEC certificates."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class HECIssueRequest(BaseModel):
    validation_id: UUID = Field(..., description="Approved validation ID")


class HECRegisterRequest(BaseModel):
    hec_id: UUID = Field(..., description="HEC certificate ID to register on-chain")


class HECResponse(BaseModel):
    hec_id: UUID
    validation_id: UUID
    plant_id: UUID
    energy_kwh: float
    certificate_hash: str = Field(..., description="SHA-256 of the canonical certificate JSON")
    status: str = Field("pending", description="pending | registered | minted | custodied | allocated | retired")
    chain: str = Field("polygon", description="Blockchain target")
    issued_at: str
    certificate_json: Optional[dict] = Field(None, description="Full certificate JSON")
    pdf_available: bool = Field(False, description="True when a PDF rendition exists")
    ipfs_json_cid: Optional[str] = Field(None, description="JSON CID")
    ipfs_pdf_cid: Optional[str] = Field(None, description="PDF CID")
    ipfs_provider: Optional[str] = Field(None, description="IPFS provider")
    registry_tx_hash: Optional[str] = Field(None, description="Certificate registry transaction hash")
    registry_block: Optional[int] = Field(None, description="Certificate registry block number")
    contract_address: Optional[str] = Field(None, description="Registry contract address")
    backing_complete: bool = Field(False, description="True when registry_tx_hash exists")
    message: str = "HEC issued successfully"


class HECVerifyResponse(BaseModel):
    verified: bool = Field(..., description="True when the IPFS JSON matches the stored hash")
    hec_id: UUID
    stored_hash: str
    recalculated_hash: str
    match: bool
    json_cid: Optional[str] = None
    pdf_cid: Optional[str] = None
    json_size_bytes: int = 0
    ipfs_provider: str = ""
    verified_at: str
    certificate_json: Optional[dict] = None
    reason: str = ""


class OnChainVerifyResponse(BaseModel):
    exists: bool = Field(..., description="True when the certificate hash is registered on-chain")
    certificate_hash: str
    ipfs_cid: str = ""
    registered_at: int = 0
    block_number: int = 0
    contract_address: str = ""
    chain: str = ""
    provider: str = ""
    backing_complete: bool = False


class HECError(BaseModel):
    status: str = "error"
    error: str
    detail: Optional[str] = None

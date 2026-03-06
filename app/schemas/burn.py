"""Pydantic schemas para burn de HECs."""
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class BurnRequest(BaseModel):
    quantity: int = Field(..., ge=1, description="Quantidade de HECs a queimar")
    reason: str = Field(
        "voluntary",
        description="Motivo: offset | retirement | voluntary",
    )


class BurnCertificateResponse(BaseModel):
    burn_id: UUID
    user_id: UUID
    quantity: int
    energy_kwh: float
    certificate_hash: str = Field(..., description="SHA-256 do burn certificate")
    certificate_json: Optional[dict] = None
    # IPFS
    ipfs_json_cid: Optional[str] = None
    ipfs_pdf_cid: Optional[str] = None
    ipfs_provider: Optional[str] = None
    # On-chain
    registry_tx_hash: Optional[str] = None
    registry_block: Optional[int] = None
    contract_address: Optional[str] = None
    chain: Optional[str] = None
    # Details
    burned_hec_ids: List[str] = Field(default_factory=list)
    reason: str = "voluntary"
    status: str = "burned"
    irreversible: bool = True
    burned_at: str = ""
    wallet_hec_after: int = 0
    wallet_energy_after: float = 0.0
    message: str = ""


class BurnListResponse(BaseModel):
    burn_id: UUID
    quantity: int
    energy_kwh: float
    certificate_hash: str
    reason: str
    status: str
    registry_tx_hash: Optional[str] = None
    burned_at: str


class BurnVerifyResponse(BaseModel):
    burn_id: UUID
    quantity: int
    energy_kwh: float
    certificate_hash: str
    ipfs_json_cid: Optional[str] = None
    ipfs_pdf_cid: Optional[str] = None
    ipfs_provider: Optional[str] = None
    registry_tx_hash: Optional[str] = None
    registry_block: Optional[int] = None
    contract_address: Optional[str] = None
    chain: Optional[str] = None
    reason: str = "voluntary"
    status: str = "burned"
    burned_at: str
    backing_complete: bool = False
    message: str = ""

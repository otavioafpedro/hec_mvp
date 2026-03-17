"""Pydantic schemas for custody-backed retirement receipts."""
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class BurnRequest(BaseModel):
    quantity: int = Field(..., ge=1, description="HEC quantity to retire")
    reason: str = Field("voluntary", description="offset | retirement | voluntary")
    claimant_wallet: Optional[str] = Field(None, description="Optional claimant EVM wallet")
    beneficiary_ref: Optional[str] = Field(None, description="Economic beneficiary reference stored hashed in the receipt")
    external_operation_id: Optional[str] = Field(None, description="Client idempotency key or external operation ID")


class BurnCertificateResponse(BaseModel):
    burn_id: UUID
    user_id: UUID
    quantity: int
    energy_kwh: float
    retired_mhec: int
    certificate_hash: str
    certificate_json: Optional[dict] = None
    ipfs_json_cid: Optional[str] = None
    ipfs_pdf_cid: Optional[str] = None
    ipfs_provider: Optional[str] = None
    registry_tx_hash: Optional[str] = None
    registry_block: Optional[int] = None
    contract_address: Optional[str] = None
    chain: Optional[str] = None
    burned_hec_ids: List[str] = Field(default_factory=list)
    retirement_event_ids: List[str] = Field(default_factory=list)
    receipt_token_ids: List[int] = Field(default_factory=list)
    claimant_wallet: Optional[str] = None
    beneficiary_ref: Optional[str] = None
    beneficiary_ref_hash: Optional[str] = None
    external_operation_id: Optional[str] = None
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
    retired_mhec: int
    certificate_hash: str
    reason: str
    status: str
    registry_tx_hash: Optional[str] = None
    burned_at: str


class BurnVerifyResponse(BaseModel):
    burn_id: UUID
    quantity: int
    energy_kwh: float
    retired_mhec: int
    certificate_hash: str
    ipfs_json_cid: Optional[str] = None
    ipfs_pdf_cid: Optional[str] = None
    ipfs_provider: Optional[str] = None
    registry_tx_hash: Optional[str] = None
    registry_block: Optional[int] = None
    contract_address: Optional[str] = None
    chain: Optional[str] = None
    claimant_wallet: Optional[str] = None
    beneficiary_ref_hash: Optional[str] = None
    external_operation_id: Optional[str] = None
    reason: str = "voluntary"
    status: str = "burned"
    burned_at: str
    backing_complete: bool = False
    message: str = ""

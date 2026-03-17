"""Pydantic schemas for HEC lot custody inventory."""
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class LotCreateRequest(BaseModel):
    hec_ids: List[UUID] = Field(..., min_length=1, description="HEC IDs to include in the custody lot")
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    price_per_kwh: Optional[float] = Field(None, ge=0, description="BRL price per kWh")


class LotCertificateSummary(BaseModel):
    hec_id: UUID
    energy_kwh: float
    certificate_hash: str
    ipfs_json_cid: Optional[str] = None
    registry_tx_hash: Optional[str] = None
    status: str


class LotResponse(BaseModel):
    lot_id: UUID
    name: str
    description: Optional[str] = None
    total_quantity: int = Field(..., description="Total HEC quantity in custody")
    available_quantity: int = Field(..., description="Quantity still available for allocation")
    total_energy_kwh: float = Field(..., description="Total energy represented by the lot")
    price_per_kwh: Optional[float] = Field(None, description="BRL price per kWh")
    status: str = Field("open", description="open | closed")
    custody_mode: str = Field("platform_custody", description="platform_custody | external_custody")
    transferability_policy: str = Field("non_transferable", description="non_transferable | protocol_only")
    inventory_status: str = Field("issued", description="pending | issued | suspended | revoked")
    batch_hash: Optional[str] = None
    lot_manifest_cid: Optional[str] = None
    onchain_batch_token_id: Optional[int] = None
    onchain_issued_tx_hash: Optional[str] = None
    certificates: Optional[List[LotCertificateSummary]] = Field(None, description="Optional HEC summary list")
    backing_complete: bool = Field(True, description="True when certificates and lot inventory were anchored")
    created_at: str
    message: str = ""

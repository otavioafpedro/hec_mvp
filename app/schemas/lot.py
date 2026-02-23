"""Pydantic schemas para lotes HEC."""
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class LotCreateRequest(BaseModel):
    """Request para criação de lote backed."""
    hec_ids: List[UUID] = Field(..., min_length=1, description="Lista de HEC IDs a incluir")
    name: str = Field(..., min_length=1, max_length=255, description="Nome do lote")
    description: Optional[str] = Field(None, description="Descrição opcional")
    price_per_kwh: Optional[float] = Field(None, ge=0, description="Preço por kWh em BRL")


class LotCertificateSummary(BaseModel):
    """Resumo de um HEC dentro de um lote."""
    hec_id: UUID
    energy_kwh: float
    certificate_hash: str
    ipfs_json_cid: Optional[str] = None
    registry_tx_hash: Optional[str] = None
    status: str


class LotResponse(BaseModel):
    """Resposta da criação/consulta de lote."""
    lot_id: UUID
    name: str
    description: Optional[str] = None
    total_quantity: int = Field(..., description="Quantidade total de HECs no lote")
    available_quantity: int = Field(..., description="Quantidade disponível")
    total_energy_kwh: float = Field(..., description="Soma total kWh")
    price_per_kwh: Optional[float] = Field(None, description="Preço por kWh em BRL")
    status: str = Field("open", description="open | closed | listed | sold")
    certificates: Optional[List[LotCertificateSummary]] = Field(
        None, description="Resumo dos HECs (incluído na consulta)")
    backing_complete: bool = Field(True, description="True se todos os HECs têm backing completo")
    created_at: str
    message: str = ""

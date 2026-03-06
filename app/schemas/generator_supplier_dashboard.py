from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class SupplierQSVStatus(BaseModel):
    ccee: bool = True
    mqtt: bool = True
    meter: bool = True
    sat: bool = True


class SupplierPlantDashboardItem(BaseModel):
    id: UUID
    name: str
    type: str
    capacity: str
    location: str
    status: Literal["online", "maintenance"]
    genToday: float = 0.0
    genMonth: float = 0.0
    efficiency: float = 0.0
    hecsEmitted: int = 0
    hecsAvailable: int = 0
    revenue: float = 0.0
    qsv: SupplierQSVStatus = Field(default_factory=SupplierQSVStatus)
    lastSync: str = "-"


class SupplierRecentHecItem(BaseModel):
    id: str
    plant: str
    hour: str
    mwh: float = 0.0
    price: str = "R$ 0,00"
    status: Literal["minted", "available"] = "available"
    merkle: str = "-"
    buyer: str = "-"
    tier: str = "Tier 3"


class SupplierHourlyGenerationItem(BaseModel):
    hour: str
    solar: float = 0.0
    wind: float = 0.0


class GeneratorSupplierDashboardResponse(BaseModel):
    profile_status: str
    split_percentage: int = 70
    plants: list[SupplierPlantDashboardItem] = Field(default_factory=list)
    recent_hecs: list[SupplierRecentHecItem] = Field(default_factory=list)
    hourly_generation: list[SupplierHourlyGenerationItem] = Field(default_factory=list)
    generated_at: datetime

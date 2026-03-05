from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class GeneratorPlantInput(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    absolar_id: Optional[str] = Field(default=None, max_length=100)
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    capacity_kw: float = Field(..., gt=0)
    inverter_brand: Optional[str] = Field(default=None, max_length=100)
    inverter_model: Optional[str] = Field(default=None, max_length=100)


class GeneratorInverterConnectionInput(BaseModel):
    provider_name: str = Field(..., min_length=2, max_length=100)
    integration_mode: Literal["direct_api", "vendor_partner"]
    external_account_ref: Optional[str] = Field(default=None, max_length=255)
    inverter_serial: Optional[str] = Field(default=None, max_length=100)
    consent_accepted: bool = Field(
        ...,
        description="Aceite para leitura de dados de geracao do inversor",
    )


class AddGeneratorConnectionRequest(GeneratorInverterConnectionInput):
    plant_id: Optional[UUID] = None


class GeneratorRegisterRequest(BaseModel):
    email: str = Field(..., min_length=5)
    name: str = Field(..., min_length=2, max_length=255)
    password: str = Field(..., min_length=6)
    person_type: Literal["PF", "PJ"]
    document_id: str = Field(..., min_length=11, max_length=32)
    legal_name: Optional[str] = Field(default=None, max_length=255)
    trade_name: Optional[str] = Field(default=None, max_length=255)
    phone: Optional[str] = Field(default=None, max_length=30)
    attribute_assignment_accepted: bool = Field(
        ...,
        description="Aceite da cessao do atributo ambiental da energia gerada",
    )
    plant: GeneratorPlantInput
    inverter_connection: GeneratorInverterConnectionInput

    @model_validator(mode="after")
    def validate_business_rules(self):
        if not self.attribute_assignment_accepted:
            raise ValueError("attribute_assignment_accepted deve ser true para concluir o cadastro")
        if not self.inverter_connection.consent_accepted:
            raise ValueError("consent_accepted deve ser true para habilitar integracao de inversor")
        if self.person_type == "PJ" and not self.legal_name:
            raise ValueError("legal_name e obrigatorio para pessoa juridica (PJ)")
        return self


class GeneratorConnectionResponse(BaseModel):
    connection_id: UUID
    profile_id: UUID
    plant_id: Optional[UUID] = None
    provider_name: str
    integration_mode: str
    external_account_ref: Optional[str] = None
    inverter_serial: Optional[str] = None
    consent_accepted: bool
    consented_at: Optional[datetime] = None
    connection_status: str
    last_sync_at: Optional[datetime] = None
    created_at: datetime


class GeneratorOnboardingResponse(BaseModel):
    user_id: UUID
    profile_id: UUID
    email: str
    name: str
    role: str
    person_type: str
    document_id: str
    legal_name: Optional[str] = None
    trade_name: Optional[str] = None
    phone: Optional[str] = None
    onboarding_status: str
    attribute_assignment_accepted: bool
    assignment_accepted_at: Optional[datetime] = None
    plant_id: UUID
    plant_name: str
    token: Optional[str] = None
    connections: list[GeneratorConnectionResponse] = Field(default_factory=list)
    message: str = ""

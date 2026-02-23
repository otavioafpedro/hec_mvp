"""Pydantic schemas para ingestão de telemetria."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class TelemetryRequest(BaseModel):
    """
    Payload de ingestão de telemetria do inversor solar.
    Exige assinatura ECDSA + nonce para anti-replay (Fortaleza Lógica Camada 1).
    """
    plant_id: UUID = Field(..., description="UUID da usina solar")
    timestamp: str = Field(
        ...,
        description="Timestamp ISO-8601 da leitura (ex: 2026-02-23T14:30:00Z)",
    )
    power_kw: float = Field(..., ge=0, description="Potência instantânea em kW")
    energy_kwh: float = Field(..., ge=0, description="Energia acumulada em kWh")

    # Segurança — Fortaleza Lógica
    signature: str = Field(
        ...,
        description="Assinatura ECDSA (hex) do payload canônico",
    )
    public_key: str = Field(
        ...,
        description="Chave pública PEM (EC secp256k1)",
    )
    nonce: str = Field(
        ...,
        min_length=8,
        max_length=64,
        description="Nonce único para anti-replay (válido por 60 segundos)",
    )

    # Opcionais
    voltage_v: Optional[float] = Field(None, ge=0, description="Tensão em V")
    temperature_c: Optional[float] = Field(None, description="Temperatura em °C")
    irradiance_wm2: Optional[float] = Field(None, ge=0, description="Irradiância W/m²")


class TelemetryResponse(BaseModel):
    """Resposta de ingestão com resultado de todas as 5 camadas + score oficial."""
    status: str = "accepted"
    telemetry_id: UUID
    plant_id: UUID
    timestamp: str
    payload_sha256: str
    # Camada 2 — NTP
    ntp_drift_ms: float = Field(..., description="Drift NTP medido em milissegundos")
    ntp_pass: bool = Field(..., description="True se |drift| <= 5ms")
    # Camada 3 — Física Teórica
    theoretical_max_kwh: float = Field(..., description="Geração máxima teórica (kWh)")
    physics_pass: bool = Field(..., description="True se energy_kwh <= theoretical_max_kwh")
    solar_elevation_deg: float = Field(..., description="Elevação solar (graus)")
    ghi_clear_sky_wm2: float = Field(..., description="Irradiância clear-sky estimada (W/m²)")
    # Camada 4 — Satélite
    satellite_ghi_wm2: float = Field(..., description="Irradiância GHI medida por satélite (W/m²)")
    satellite_max_kwh: float = Field(..., description="Geração máx baseada em satélite (kWh)")
    satellite_pass: bool = Field(..., description="True se energy_kwh <= satellite_max_kwh")
    cloud_cover_pct: float = Field(..., description="Cobertura de nuvens (%)")
    # Camada 5 — Consenso Granular
    consensus_pass: Optional[bool] = Field(None, description="True=ok, False=divergente, None=inconclusivo")
    consensus_deviation_pct: Optional[float] = Field(None, description="Desvio da mediana (%)")
    consensus_neighbors: int = Field(0, description="Vizinhas usadas no consenso")
    # Confidence Score Oficial
    validation_id: Optional[UUID] = Field(None, description="ID da validação criada")
    confidence_score: float = Field(..., description="Score consolidado 0-100 SENTINEL AGIS")
    confidence_breakdown: Optional[dict] = Field(None, description="Pontuação por camada")
    # HEC Certificate (auto-issued on APPROVED)
    hec_id: Optional[UUID] = Field(None, description="ID do certificado HEC (se emitido)")
    certificate_hash: Optional[str] = Field(None, description="SHA-256 do certificado HEC")
    registry_tx_hash: Optional[str] = Field(None, description="Transaction hash do registro on-chain")
    backing_complete: bool = Field(False, description="True se registry_tx_hash existir")
    message: str = "Telemetria ingerida com sucesso"


class TelemetryError(BaseModel):
    """Resposta de erro na ingestão."""
    status: str = "rejected"
    error: str
    detail: Optional[str] = None

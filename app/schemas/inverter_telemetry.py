from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class InverterTelemetryRequest(BaseModel):
    device_uuid: UUID = Field(..., description="UUID do dispositivo em devices.uuid (MariaDB)")
    timestamp: datetime = Field(..., description="Timestamp ISO-8601 com timezone")

    power_ac_w: float | None = Field(None, ge=0)
    power_dc_w: float | None = Field(None, ge=0)
    energy_today_wh: int | None = Field(None, ge=0)
    energy_total_wh: int | None = Field(None, ge=0)

    voltage_ac_v: float | None = Field(None, ge=0)
    current_ac_a: float | None = Field(None, ge=0)
    voltage_dc_v: float | None = Field(None, ge=0)
    current_dc_a: float | None = Field(None, ge=0)

    frequency_hz: float | None = Field(None, ge=0)
    efficiency_pct: float | None = Field(None, ge=0, le=100)
    temperature_c: float | None = None

    status_code: int | None = Field(None, ge=0, le=32767)
    error_code: int | None = Field(None, ge=0, le=32767)
    is_online: bool = True
    data_quality: int = Field(100, ge=0, le=100)

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("timestamp must include timezone, e.g. 2026-03-02T13:45:00Z")
        return value


class InverterTelemetryResponse(BaseModel):
    status: str = "accepted"
    device_uuid: UUID
    device_id: int
    site_id: int
    timestamp_utc: datetime
    device_sync_ok: bool = True
    message: str = "Telemetry persisted"

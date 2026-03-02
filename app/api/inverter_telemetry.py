from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.db.soa_session import get_mysql_db, get_timeseries_db
from app.schemas.inverter_telemetry import (
    InverterTelemetryRequest,
    InverterTelemetryResponse,
)

router = APIRouter(prefix="/soa/v1", tags=["SOA Ingest"])


DEVICE_LOOKUP_QUERY = text(
    """
    SELECT
        d.id AS device_id,
        d.uuid AS device_uuid,
        d.site_id AS site_id,
        d.device_type AS device_type,
        d.status AS device_status,
        s.status AS site_status
    FROM devices d
    INNER JOIN sites s ON s.id = d.site_id
    WHERE d.uuid = :device_uuid
    LIMIT 1
    """
)


INSERT_INVERTER_TELEMETRY_QUERY = text(
    """
    INSERT INTO inverter_telemetry (
        ts,
        device_id,
        site_id,
        power_ac_w,
        power_dc_w,
        energy_today_wh,
        energy_total_wh,
        voltage_ac_v,
        current_ac_a,
        voltage_dc_v,
        current_dc_a,
        frequency_hz,
        efficiency_pct,
        temperature_c,
        status_code,
        error_code,
        is_online,
        data_quality
    ) VALUES (
        :ts,
        :device_id,
        :site_id,
        :power_ac_w,
        :power_dc_w,
        :energy_today_wh,
        :energy_total_wh,
        :voltage_ac_v,
        :current_ac_a,
        :voltage_dc_v,
        :current_dc_a,
        :frequency_hz,
        :efficiency_pct,
        :temperature_c,
        :status_code,
        :error_code,
        :is_online,
        :data_quality
    )
    """
)


UPDATE_DEVICE_LAST_SEEN_QUERY = text(
    """
    UPDATE devices
       SET last_seen_at = UTC_TIMESTAMP(),
           status = :status,
           updated_at = UTC_TIMESTAMP()
     WHERE id = :device_id
    """
)


@router.post(
    "/inverter-telemetry",
    response_model=InverterTelemetryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest inverter telemetry (MariaDB + Timeseries split)",
)
def ingest_inverter_telemetry(
    payload: InverterTelemetryRequest,
    mysql_db: Session = Depends(get_mysql_db),
    timeseries_db: Session = Depends(get_timeseries_db),
):
    if not settings.SOA_ENABLE_INGEST:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SOA ingest disabled. Set SOA_ENABLE_INGEST=true.",
        )

    try:
        device = (
            mysql_db.execute(
                DEVICE_LOOKUP_QUERY,
                {"device_uuid": str(payload.device_uuid)},
            )
            .mappings()
            .first()
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"MariaDB unavailable while validating device: {exc.__class__.__name__}",
        ) from exc

    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Device {payload.device_uuid} not found in MariaDB devices table.",
        )

    if device["device_type"] != "inverter":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Device {payload.device_uuid} is not an inverter.",
        )

    if device["site_status"] == "decommissioned":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Site for device {payload.device_uuid} is decommissioned.",
        )

    if device["device_status"] == "retired":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Device {payload.device_uuid} is retired.",
        )

    telemetry_row = {
        "ts": payload.timestamp.astimezone(timezone.utc),
        "device_id": int(device["device_id"]),
        "site_id": int(device["site_id"]),
        "power_ac_w": payload.power_ac_w,
        "power_dc_w": payload.power_dc_w,
        "energy_today_wh": payload.energy_today_wh,
        "energy_total_wh": payload.energy_total_wh,
        "voltage_ac_v": payload.voltage_ac_v,
        "current_ac_a": payload.current_ac_a,
        "voltage_dc_v": payload.voltage_dc_v,
        "current_dc_a": payload.current_dc_a,
        "frequency_hz": payload.frequency_hz,
        "efficiency_pct": payload.efficiency_pct,
        "temperature_c": payload.temperature_c,
        "status_code": payload.status_code,
        "error_code": payload.error_code,
        "is_online": payload.is_online,
        "data_quality": payload.data_quality,
    }

    try:
        timeseries_db.execute(INSERT_INVERTER_TELEMETRY_QUERY, telemetry_row)
        timeseries_db.commit()
    except SQLAlchemyError as exc:
        timeseries_db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to persist inverter telemetry in PostgreSQL: {exc.__class__.__name__}",
        ) from exc

    device_sync_ok = True
    sync_status = "online" if payload.is_online else "offline"
    try:
        mysql_db.execute(
            UPDATE_DEVICE_LAST_SEEN_QUERY,
            {"status": sync_status, "device_id": int(device["device_id"])},
        )
        mysql_db.commit()
    except SQLAlchemyError:
        mysql_db.rollback()
        device_sync_ok = False

    message = "Telemetry persisted in PostgreSQL Timeseries and device heartbeat synced."
    if not device_sync_ok:
        message = (
            "Telemetry persisted in PostgreSQL Timeseries, "
            "but MariaDB heartbeat sync failed."
        )

    return InverterTelemetryResponse(
        status="accepted",
        device_uuid=payload.device_uuid,
        device_id=int(device["device_id"]),
        site_id=int(device["site_id"]),
        timestamp_utc=telemetry_row["ts"],
        device_sync_ok=device_sync_ok,
        message=message,
    )

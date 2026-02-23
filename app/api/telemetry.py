"""
Endpoint POST /telemetry — Ingestão segura de telemetria solar.

Implementa a Fortaleza Lógica:
  Camada 1: Valida assinatura ECDSA + anti-replay nonce + SHA-256
  Camada 2: Verificação NTP Blindada ±5ms (drift → status REVIEW)
  Camada 3: Validação Física Teórica pvlib (energy > max → REVIEW)
  Camada 4: Validação Satélite — cross-validation com irradiância orbital
  Camada 5: Consenso Granular — comparação geoespacial com vizinhas 5km
"""
import uuid
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.models import Plant, Telemetry, Validation
from app.schemas.telemetry import TelemetryRequest, TelemetryResponse
from app.security import (
    canonical_payload,
    sha256_hash,
    verify_ecdsa_signature,
    check_nonce_replay,
    register_nonce,
    check_ntp_drift,
    NTP_MAX_DRIFT_MS,
    _default_server_now,
)
from app.physics import compute_theoretical_max
from app.satellite import validate_satellite
from app.consensus import validate_consensus
from app.confidence import calculate_confidence
from app.hec_generator import issue_hec

router = APIRouter()

# ---------------------------------------------------------------------------
# Injeção de dependência para relógio do servidor (testável)
# ---------------------------------------------------------------------------
_server_now_fn: Callable[[], datetime] = _default_server_now


def set_server_now_fn(fn: Callable[[], datetime]) -> None:
    """Permite injetar relógio customizado (para testes)."""
    global _server_now_fn
    _server_now_fn = fn


def reset_server_now_fn() -> None:
    """Restaura relógio real."""
    global _server_now_fn
    _server_now_fn = _default_server_now


@router.post(
    "/telemetry",
    response_model=TelemetryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingestão segura de telemetria",
    description=(
        "Recebe dados do inversor solar com assinatura ECDSA e nonce anti-replay "
        "(C1), verifica drift NTP ±5ms (C2), valida física teórica (C3), "
        "cross-valida com satélite (C4), consenso geoespacial 5km (C5)."
    ),
)
def ingest_telemetry(payload: TelemetryRequest, db: Session = Depends(get_db)):
    """
    Pipeline de ingestão — Fortaleza Lógica:
      1. Verifica se plant_id existe
      2. Monta payload canônico
      3. Verifica assinatura ECDSA             (Camada 1)
      4. Verifica nonce anti-replay 60s        (Camada 1)
      5. Verifica drift NTP ±5ms               (Camada 2)
      6. Validação física teórica pvlib        (Camada 3)
      7. Cross-validation satélite             (Camada 4)
      8. Consenso granular geoespacial 5km     (Camada 5)
      9. Gera SHA-256
     10. Persiste telemetry + validation + nonce
    """
    plant_id_str = str(payload.plant_id)

    # ── 1. Verificar se a planta existe ──────────────────────────────
    plant = db.query(Plant).filter(Plant.plant_id == payload.plant_id).first()
    if not plant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Planta {plant_id_str} não encontrada",
        )

    # ── 2. Payload canônico (determinístico, ordenado) ───────────────
    canon = canonical_payload(
        plant_id=plant_id_str,
        timestamp=payload.timestamp,
        power_kw=payload.power_kw,
        energy_kwh=payload.energy_kwh,
        nonce=payload.nonce,
    )

    # ── 3. Camada 1: Validar assinatura ECDSA ────────────────────────
    sig_valid = verify_ecdsa_signature(
        public_key_pem=payload.public_key,
        signature_hex=payload.signature,
        message=canon,
    )
    if not sig_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Assinatura ECDSA inválida — pacote descartado na borda (Fortaleza Lógica C1)",
        )

    # ── 4. Camada 1: Anti-replay nonce 60s ───────────────────────────
    if check_nonce_replay(db, nonce=payload.nonce, plant_id=plant_id_str):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Nonce já utilizado — replay attack detectado (Fortaleza Lógica C1)",
        )

    # ── 5. Camada 2: NTP Blindada ±5ms ──────────────────────────────
    ntp_pass, drift_ms, server_time = check_ntp_drift(
        payload_timestamp=payload.timestamp,
        max_drift_ms=NTP_MAX_DRIFT_MS,
        server_now_fn=_server_now_fn,
    )

    # ── 6. Camada 3: Validação Física Teórica (pvlib / Patente 33) ──
    try:
        ts = datetime.fromisoformat(payload.timestamp.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Timestamp inválido — use ISO-8601 (ex: 2026-02-23T14:30:00Z)",
        )

    physics = compute_theoretical_max(
        lat=float(plant.lat),
        lng=float(plant.lng),
        capacity_kw=float(plant.capacity_kw),
        timestamp=ts,
        reported_kwh=payload.energy_kwh,
    )

    # ── 7. Camada 4: Cross-validation Satélite ──────────────────────
    satellite = validate_satellite(
        lat=float(plant.lat),
        lng=float(plant.lng),
        capacity_kw=float(plant.capacity_kw),
        timestamp=ts,
        reported_kwh=payload.energy_kwh,
    )

    # ── 8. Camada 5: Consenso Granular Geoespacial ──────────────────
    consensus = validate_consensus(
        db=db,
        plant=plant,
        energy_kwh=payload.energy_kwh,
        reference_time=ts,
    )

    # ── 9. SHA-256 do payload para integridade ───────────────────────
    payload_hash = sha256_hash(canon)

    # ── 10. Persistir telemetry + validation + nonce ─────────────────
    telemetry_id = uuid.uuid4()
    validation_id = uuid.uuid4()

    telemetry_record = Telemetry(
        id=telemetry_id,
        time=ts,
        plant_id=payload.plant_id,
        power_kw=payload.power_kw,
        energy_kwh=payload.energy_kwh,
        voltage_v=payload.voltage_v,
        temperature_c=payload.temperature_c,
        irradiance_wm2=payload.irradiance_wm2,
        source="api",
        pre_commitment_hash=payload.signature,
        ntp_delta_ms=drift_ms,
        ntp_pass=ntp_pass,
        raw_payload={
            "power_kw": payload.power_kw,
            "energy_kwh": payload.energy_kwh,
            "voltage_v": payload.voltage_v,
            "temperature_c": payload.temperature_c,
            "irradiance_wm2": payload.irradiance_wm2,
        },
        payload_sha256=payload_hash,
        nonce=payload.nonce,
    )

    # ── Anomaly flags consolidadas ───────────────────────────────────
    anomaly_flags = {}
    if not ntp_pass:
        anomaly_flags["ntp_drift_exceeded"] = f"{drift_ms:+.3f}ms"
    if not physics.physics_pass:
        anomaly_flags["energy_exceeds_theoretical"] = {
            "reported_kwh": payload.energy_kwh,
            "theoretical_max_kwh": physics.theoretical_max_kwh,
        }
    if not satellite.satellite_pass:
        anomaly_flags["energy_exceeds_satellite"] = {
            "reported_kwh": payload.energy_kwh,
            "satellite_max_kwh": satellite.satellite_max_kwh,
            "satellite_ghi_wm2": satellite.satellite_ghi_wm2,
        }
    if satellite.high_generation_low_sun:
        anomaly_flags["high_generation_low_irradiance"] = {
            "reported_kwh": payload.energy_kwh,
            "satellite_ghi_wm2": satellite.satellite_ghi_wm2,
            "cloud_cover_pct": satellite.cloud_cover_pct,
            "severity": "high",
        }
    if consensus.consensus_pass is False:
        anomaly_flags["consensus_divergence"] = {
            "deviation_pct": consensus.deviation_pct,
            "median_ratio": consensus.median_ratio,
            "plant_ratio": consensus.plant_ratio,
            "neighbors_used": consensus.neighbors_used,
        }

    # ── Satellite-specific flags for DB column ───────────────────────
    sat_flags = {}
    if satellite.low_irradiance:
        sat_flags["low_irradiance"] = True
    if satellite.high_generation_low_sun:
        sat_flags["high_generation_low_sun"] = True

    # ── Consensus details for DB column ──────────────────────────────
    consensus_details = {
        "reason": consensus.reason,
        "neighbors": [
            {
                "plant_id": str(n.plant_id),
                "name": n.plant_name,
                "distance_km": n.distance_km,
                "ratio": n.normalized_ratio,
                "energy_kwh": n.energy_kwh,
            }
            for n in consensus.neighbors
        ],
    } if consensus.neighbors else None

    # ── Confidence Score Oficial (SENTINEL AGIS 2.0) ────────────────
    conf = calculate_confidence(
        signature_valid=True,   # Se chegou aqui, ECDSA passou (step 3)
        ntp_pass=ntp_pass,
        physics_pass=physics.physics_pass,
        satellite_pass=satellite.satellite_pass,
        consensus_pass=consensus.consensus_pass,
    )

    confidence = conf.score
    val_status = conf.status

    validation_record = Validation(
        validation_id=validation_id,
        plant_id=payload.plant_id,
        telemetry_id=telemetry_id,
        period_start=ts,
        period_end=ts + timedelta(hours=1),
        energy_kwh=payload.energy_kwh,
        confidence_score=confidence,
        anomaly_flags=anomaly_flags if anomaly_flags else None,
        status=val_status,
        ntp_pass=ntp_pass,
        ntp_drift_ms=drift_ms,
        # Camada 3
        theoretical_max_kwh=physics.theoretical_max_kwh,
        theoretical_max_kw=physics.theoretical_max_kw,
        ghi_clear_sky_wm2=physics.ghi_clear_sky_wm2,
        solar_elevation_deg=physics.solar_elevation_deg,
        physics_pass=physics.physics_pass,
        physics_method=physics.method,
        # Camada 4
        satellite_ghi_wm2=satellite.satellite_ghi_wm2,
        satellite_source=satellite.satellite_source,
        satellite_max_kwh=satellite.satellite_max_kwh,
        satellite_pass=satellite.satellite_pass,
        cloud_cover_pct=satellite.cloud_cover_pct,
        satellite_flags=sat_flags if sat_flags else None,
        # Camada 5
        consensus_pass=consensus.consensus_pass,
        consensus_deviation_pct=consensus.deviation_pct,
        consensus_median_ratio=consensus.median_ratio,
        consensus_plant_ratio=consensus.plant_ratio,
        consensus_neighbors=consensus.neighbors_used,
        consensus_radius_km=consensus.radius_km,
        consensus_details=consensus_details,
        # Confidence breakdown stored in validation_details
        validation_details={
            "confidence_breakdown": {
                "signature": conf.signature_score,
                "ntp": conf.ntp_score,
                "physics": conf.physics_score,
                "satellite": conf.satellite_score,
                "consensus": conf.consensus_score,
            },
            "weights": conf.weights,
            "thresholds": conf.thresholds,
        },
        sentinel_version="SENTINEL-AGIS-2.0",
    )

    db.add(telemetry_record)
    db.add(validation_record)
    register_nonce(db, nonce=payload.nonce, plant_id=plant_id_str)
    db.commit()

    # ── 11. Auto-issue HEC para APPROVED ─────────────────────────────
    hec_result = None
    if val_status == "approved":
        try:
            hec_result = issue_hec(db, plant, validation_record)
            db.commit()
        except Exception:
            db.rollback()  # HEC failure shouldn't block telemetry

    # ── Build response status & message ──────────────────────────────
    # Priority: most severe layer first
    if conf.status == "rejected":
        # Find dominant failure
        failures = []
        if not physics.physics_pass:
            failures.append(f"física ({payload.energy_kwh:.1f}>{physics.theoretical_max_kwh:.1f} kWh)")
        if not ntp_pass:
            failures.append(f"NTP ({drift_ms:+.1f}ms)")
        if not satellite.satellite_pass:
            failures.append(f"satélite ({payload.energy_kwh:.1f}>{satellite.satellite_max_kwh:.1f} kWh)")
        if consensus.consensus_pass is False:
            failures.append(f"consenso ({consensus.deviation_pct:.0f}%)")
        resp_status = "rejected"
        message = (
            f"REJECTED — Score {confidence:.0f}/100 "
            f"[falhas: {', '.join(failures)}] "
            f"(SENTINEL AGIS 2.0)"
        )
    elif conf.status == "review":
        failures = []
        if not ntp_pass:
            failures.append(f"NTP {drift_ms:+.1f}ms")
        if not satellite.satellite_pass:
            failures.append(f"satélite")
        if consensus.consensus_pass is False:
            failures.append(f"consenso {consensus.deviation_pct:.0f}%")
        resp_status = "review"
        message = (
            f"REVIEW — Score {confidence:.0f}/100 "
            f"[{', '.join(failures)}] "
            f"(SENTINEL AGIS 2.0)"
        )
    else:
        resp_status = "accepted"
        consensus_note = ""
        if consensus.consensus_pass is True:
            consensus_note = f", consenso {consensus.deviation_pct:.1f}%"
        elif consensus.consensus_pass is None:
            consensus_note = ", consenso N/A"
        hec_note = ""
        if hec_result:
            if hec_result.registry_tx_hash:
                hec_note = (
                    f" → HEC {str(hec_result.hec_id)[:8]} REGISTERED "
                    f"tx:{hec_result.registry_tx_hash[:10]}..."
                )
            else:
                hec_note = f" → HEC {str(hec_result.hec_id)[:8]} PENDING"
        message = (
            f"APPROVED — Score {confidence:.0f}/100 "
            f"(C1✓ C2✓ C3✓ C4✓{consensus_note}){hec_note} "
            f"(SENTINEL AGIS 2.0)"
        )

    # ── Breakdown para resposta ──────────────────────────────────────
    breakdown = {
        "C1_signature": conf.signature_score,
        "C2_ntp": conf.ntp_score,
        "C3_physics": conf.physics_score,
        "C4_satellite": conf.satellite_score,
        "C5_consensus": conf.consensus_score,
        "weights": conf.weights,
    }

    return TelemetryResponse(
        status=resp_status,
        telemetry_id=telemetry_id,
        plant_id=payload.plant_id,
        timestamp=payload.timestamp,
        payload_sha256=payload_hash,
        ntp_drift_ms=round(drift_ms, 4),
        ntp_pass=ntp_pass,
        theoretical_max_kwh=physics.theoretical_max_kwh,
        physics_pass=physics.physics_pass,
        solar_elevation_deg=physics.solar_elevation_deg,
        ghi_clear_sky_wm2=physics.ghi_clear_sky_wm2,
        satellite_ghi_wm2=satellite.satellite_ghi_wm2,
        satellite_max_kwh=satellite.satellite_max_kwh,
        satellite_pass=satellite.satellite_pass,
        cloud_cover_pct=satellite.cloud_cover_pct,
        consensus_pass=consensus.consensus_pass,
        consensus_deviation_pct=consensus.deviation_pct,
        consensus_neighbors=consensus.neighbors_used,
        validation_id=validation_id,
        confidence_score=confidence,
        confidence_breakdown=breakdown,
        hec_id=hec_result.hec_id if hec_result else None,
        certificate_hash=hec_result.certificate_hash if hec_result else None,
        registry_tx_hash=hec_result.registry_tx_hash if hec_result else None,
        backing_complete=hec_result.registry_tx_hash is not None if hec_result else False,
        message=message,
    )

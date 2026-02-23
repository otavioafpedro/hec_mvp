"""
HEC Certificate Generator — Gerador de Certificados de Energia Habilitada

Para cada validação APPROVED, gera:
  1. JSON Certificate — dados estruturados do certificado
  2. PDF Certificate — documento visual com QR code placeholder
  3. SHA-256 Hash — hash do certificado completo (imutável)
  4. Persiste em hec_certificates com status PENDING

Campos do certificado:
  - hec_id: UUID único do certificado
  - plant_id, plant_name, lat, lng
  - energy_kwh: energia validada
  - period_start, period_end
  - confidence_score + breakdown por camada
  - validation_id, sentinel_version
  - certificate_hash: SHA-256(certificado JSON canônico)
  - issued_at: timestamp de emissão

Fluxo:
  1. Validação passa com status=approved (score >= 95)
  2. build_certificate_json() monta o JSON canônico
  3. compute_certificate_hash() calcula SHA-256 do JSON
  4. generate_certificate_pdf() gera PDF com reportlab
  5. issue_hec() persiste tudo em hec_certificates
"""
import hashlib
import io
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.models import (
    Plant, Validation, HECCertificate,
)


# ---------------------------------------------------------------------------
# Resultado da emissão
# ---------------------------------------------------------------------------

@dataclass
class HECIssuanceResult:
    """Resultado da emissão de um certificado HEC."""
    hec_id: uuid.UUID
    validation_id: uuid.UUID
    plant_id: uuid.UUID
    energy_kwh: float
    certificate_hash: str       # SHA-256 do JSON canônico
    certificate_json: dict      # JSON completo do certificado
    pdf_bytes: bytes             # PDF binário
    status: str                 # "pending" | "registered"
    issued_at: datetime
    # IPFS
    ipfs_json_cid: Optional[str] = None
    ipfs_pdf_cid: Optional[str] = None
    ipfs_provider: Optional[str] = None
    # On-chain
    registry_tx_hash: Optional[str] = None
    registry_block: Optional[int] = None
    contract_address: Optional[str] = None
    registered_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Build JSON Certificate
# ---------------------------------------------------------------------------

def build_certificate_json(
    hec_id: uuid.UUID,
    plant: Plant,
    validation: Validation,
    issued_at: datetime,
) -> dict:
    """
    Monta o JSON canônico do certificado HEC.

    Ordem determinística (sorted keys) para hash reproduzível.
    """
    return {
        "certificate": {
            "hec_id": str(hec_id),
            "type": "HEC",
            "version": "1.0",
            "standard": "SENTINEL-AGIS-2.0",
        },
        "plant": {
            "plant_id": str(plant.plant_id),
            "name": plant.name,
            "absolar_id": plant.absolar_id,
            "lat": float(plant.lat),
            "lng": float(plant.lng),
            "capacity_kw": float(plant.capacity_kw),
        },
        "energy": {
            "energy_kwh": float(validation.energy_kwh),
            "period_start": validation.period_start.isoformat() + "Z",
            "period_end": validation.period_end.isoformat() + "Z",
        },
        "validation": {
            "validation_id": str(validation.validation_id),
            "confidence_score": float(validation.confidence_score),
            "status": validation.status,
            "ntp_pass": validation.ntp_pass,
            "ntp_drift_ms": validation.ntp_drift_ms,
            "physics_pass": validation.physics_pass,
            "theoretical_max_kwh": float(validation.theoretical_max_kwh) if validation.theoretical_max_kwh else None,
            "satellite_pass": validation.satellite_pass,
            "satellite_ghi_wm2": float(validation.satellite_ghi_wm2) if validation.satellite_ghi_wm2 else None,
            "consensus_pass": validation.consensus_pass,
            "consensus_deviation_pct": float(validation.consensus_deviation_pct) if validation.consensus_deviation_pct else None,
            "consensus_neighbors": validation.consensus_neighbors,
            "sentinel_version": validation.sentinel_version,
        },
        "issuance": {
            "issued_at": issued_at.isoformat() + "Z",
            "issuer": "Solar One HUB / ABSOLAR",
            "chain": "polygon",
            "status": "pending",
        },
    }


# ---------------------------------------------------------------------------
# Compute SHA-256 of canonical JSON
# ---------------------------------------------------------------------------

def compute_certificate_hash(certificate_json: dict) -> str:
    """
    Calcula SHA-256 do JSON canônico do certificado.

    Serializa com sort_keys=True + separators compactos para
    garantir hash determinístico independente de implementação.
    """
    canonical = json.dumps(
        certificate_json,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Generate PDF Certificate
# ---------------------------------------------------------------------------

def generate_certificate_pdf(
    certificate_json: dict,
    certificate_hash: str,
) -> bytes:
    """
    Gera PDF visual do certificado HEC usando reportlab.

    Layout:
      - Cabeçalho com logo placeholder + título
      - Dados da planta (nome, GPS, capacidade)
      - Dados de energia (kWh, período)
      - Score de confiança + breakdown por camada
      - Hash SHA-256 do certificado
      - QR code placeholder (área reservada)
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    w, h = A4
    c = canvas.Canvas(buffer, pagesize=A4)

    # ── Colors ──
    dark = HexColor("#1a1a2e")
    accent = HexColor("#e94560")
    green = HexColor("#0f9b58")
    gray = HexColor("#6b7280")
    light_bg = HexColor("#f8f9fa")

    cert = certificate_json["certificate"]
    plant = certificate_json["plant"]
    energy = certificate_json["energy"]
    val = certificate_json["validation"]
    issuance = certificate_json["issuance"]

    # ── Header bar ──
    c.setFillColor(dark)
    c.rect(0, h - 80, w, 80, fill=True, stroke=False)

    c.setFillColor(HexColor("#ffffff"))
    c.setFont("Helvetica-Bold", 22)
    c.drawString(30, h - 40, "CERTIFICADO HEC")
    c.setFont("Helvetica", 11)
    c.drawString(30, h - 58, f"Hydroelectric Energy Certificate — {cert['standard']}")

    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(w - 30, h - 40, f"HEC #{cert['hec_id'][:8].upper()}")
    c.setFont("Helvetica", 9)
    c.drawRightString(w - 30, h - 55, f"Emitido: {issuance['issued_at'][:10]}")

    # ── Accent line ──
    c.setStrokeColor(accent)
    c.setLineWidth(3)
    c.line(0, h - 82, w, h - 82)

    y = h - 115

    # ── Section: Planta ──
    c.setFillColor(dark)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(30, y, "USINA SOLAR")
    y -= 22

    c.setFont("Helvetica", 10)
    c.setFillColor(HexColor("#374151"))
    lines = [
        f"Nome: {plant['name']}",
        f"ID ABSOLAR: {plant.get('absolar_id', 'N/A')}",
        f"Coordenadas: {plant['lat']:.4f}, {plant['lng']:.4f}",
        f"Capacidade: {plant['capacity_kw']:.1f} kWp",
    ]
    for line in lines:
        c.drawString(40, y, line)
        y -= 16

    y -= 10

    # ── Section: Energia ──
    c.setFillColor(light_bg)
    c.rect(25, y - 58, w - 50, 70, fill=True, stroke=False)

    c.setFillColor(dark)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(30, y, "ENERGIA VALIDADA")
    y -= 24

    c.setFont("Helvetica-Bold", 28)
    c.setFillColor(green)
    c.drawString(40, y, f"{energy['energy_kwh']:.4f} kWh")
    y -= 20

    c.setFont("Helvetica", 9)
    c.setFillColor(gray)
    c.drawString(40, y, f"Período: {energy['period_start'][:19]} — {energy['period_end'][:19]}")
    y -= 30

    # ── Section: Validação ──
    c.setFillColor(dark)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(30, y, "VALIDAÇÃO SENTINEL AGIS")
    y -= 24

    # Score badge
    score = val["confidence_score"]
    score_color = green if score >= 95 else (HexColor("#f59e0b") if score >= 85 else accent)

    c.setFillColor(score_color)
    c.roundRect(40, y - 10, 100, 32, 4, fill=True, stroke=False)
    c.setFillColor(HexColor("#ffffff"))
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(90, y, f"{score:.0f}/100")

    c.setFillColor(dark)
    c.setFont("Helvetica-Bold", 12)
    status_label = val["status"].upper()
    c.drawString(155, y, status_label)

    y -= 35

    # Camadas
    c.setFont("Helvetica", 9)
    c.setFillColor(HexColor("#374151"))
    layers = [
        ("C1 Assinatura ECDSA", "✓"),
        ("C2 NTP Blindada ±5ms", "✓" if val["ntp_pass"] else "✗"),
        ("C3 Física Teórica", "✓" if val["physics_pass"] else "✗"),
        ("C4 Satélite Orbital", "✓" if val["satellite_pass"] else "✗"),
        ("C5 Consenso Granular",
         "✓" if val["consensus_pass"] is True
         else ("N/A" if val["consensus_pass"] is None else "✗")),
    ]

    for layer_name, status in layers:
        status_color = green if status == "✓" else (gray if status == "N/A" else accent)
        c.setFillColor(status_color)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(40, y, status)
        c.setFillColor(HexColor("#374151"))
        c.setFont("Helvetica", 9)
        c.drawString(60, y, layer_name)
        y -= 15

    y -= 10

    # Details
    c.setFont("Helvetica", 8)
    c.setFillColor(gray)
    detail_lines = []
    if val.get("ntp_drift_ms") is not None:
        detail_lines.append(f"NTP drift: {val['ntp_drift_ms']:+.3f}ms")
    if val.get("theoretical_max_kwh") is not None:
        detail_lines.append(f"Máx teórico: {val['theoretical_max_kwh']:.2f} kWh")
    if val.get("satellite_ghi_wm2") is not None:
        detail_lines.append(f"Satélite GHI: {val['satellite_ghi_wm2']:.0f} W/m²")
    if val.get("consensus_deviation_pct") is not None:
        detail_lines.append(f"Consenso desvio: {val['consensus_deviation_pct']:.1f}%")

    for dl in detail_lines:
        c.drawString(40, y, dl)
        y -= 12

    y -= 15

    # ── Section: Hash ──
    c.setFillColor(dark)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(30, y, "INTEGRIDADE")
    y -= 18

    c.setFillColor(light_bg)
    c.rect(25, y - 14, w - 50, 26, fill=True, stroke=False)

    c.setFont("Courier", 7)
    c.setFillColor(dark)
    c.drawString(35, y - 5, f"SHA-256: {certificate_hash}")
    y -= 35

    # ── QR Code placeholder ──
    c.setFillColor(dark)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(30, y, "VERIFICAÇÃO")
    y -= 18

    c.setStrokeColor(gray)
    c.setDash(3, 3)
    c.setLineWidth(1)
    c.rect(40, y - 65, 65, 65, fill=False, stroke=True)
    c.setDash()

    c.setFont("Helvetica", 7)
    c.setFillColor(gray)
    c.drawString(48, y - 35, "QR Code")

    c.setFont("Helvetica", 8)
    c.drawString(120, y - 15, f"Validation ID: {val['validation_id'][:8]}...")
    c.drawString(120, y - 28, f"Chain: {issuance['chain']}")
    c.drawString(120, y - 41, f"Status: {issuance['status'].upper()}")

    # ── Footer ──
    c.setStrokeColor(gray)
    c.setLineWidth(0.5)
    c.line(30, 55, w - 30, 55)

    c.setFont("Helvetica", 7)
    c.setFillColor(gray)
    c.drawString(30, 42, f"Solar One HUB / ABSOLAR — {cert['standard']}")
    c.drawRightString(w - 30, 42, f"HEC v{cert['version']} — {cert['hec_id']}")

    c.save()
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Issue HEC Certificate
# ---------------------------------------------------------------------------

def issue_hec(
    db: Session,
    plant: Plant,
    validation: Validation,
    issued_at: Optional[datetime] = None,
) -> HECIssuanceResult:
    """
    Emite um certificado HEC para uma validação APPROVED.

    Pipeline:
      1. Gera UUID do certificado
      2. Monta JSON canônico
      3. Calcula SHA-256
      4. Gera PDF
      5. Upload JSON + PDF para IPFS → CIDs
      6. Register on-chain (hash + IPFS CID) → tx_hash
      7. Persiste em hec_certificates com status=registered + CIDs + tx_hash

    Args:
        db: Sessão do banco
        plant: Planta associada
        validation: Validação APPROVED
        issued_at: Timestamp de emissão (default: now UTC)

    Returns:
        HECIssuanceResult com todos os dados do certificado

    Raises:
        ValueError: Se a validação não for APPROVED
    """
    if validation.status != "approved":
        raise ValueError(
            f"Só é possível emitir HEC para validações APPROVED. "
            f"Status atual: {validation.status}"
        )

    from app.ipfs_service import upload_certificate_to_ipfs

    issued_at = issued_at or datetime.now(timezone.utc)
    hec_id = uuid.uuid4()

    # 1. Build JSON
    cert_json = build_certificate_json(hec_id, plant, validation, issued_at)

    # 2. Compute hash
    cert_hash = compute_certificate_hash(cert_json)

    # 3. Generate PDF
    pdf_bytes = generate_certificate_pdf(cert_json, cert_hash)

    # 4. Upload to IPFS
    ipfs_result = upload_certificate_to_ipfs(
        certificate_json=cert_json,
        pdf_bytes=pdf_bytes,
        hec_id=str(hec_id),
    )

    # 5. Register on-chain
    from app.blockchain import register_on_chain
    chain_result = register_on_chain(
        certificate_hash_hex=cert_hash,
        ipfs_cid=ipfs_result.json_cid,
    )

    # 6. Persist — status=registered se tx_hash existe, senão pending
    final_status = "registered" if chain_result.tx_hash else "pending"

    hec_record = HECCertificate(
        hec_id=hec_id,
        validation_id=validation.validation_id,
        hash_sha256=cert_hash,
        energy_kwh=validation.energy_kwh,
        certificate_json=cert_json,
        chain=chain_result.chain,
        status=final_status,
        minted_at=issued_at,
        ipfs_json_cid=ipfs_result.json_cid,
        ipfs_pdf_cid=ipfs_result.pdf_cid,
        ipfs_provider=ipfs_result.provider,
        registry_tx_hash=chain_result.tx_hash,
        registry_block=chain_result.block_number,
        contract_address=chain_result.contract_address,
        registered_at=chain_result.registered_at,
    )

    db.add(hec_record)
    # Don't commit here — caller controls transaction

    return HECIssuanceResult(
        hec_id=hec_id,
        validation_id=validation.validation_id,
        plant_id=plant.plant_id,
        energy_kwh=float(validation.energy_kwh),
        certificate_hash=cert_hash,
        certificate_json=cert_json,
        pdf_bytes=pdf_bytes,
        status=final_status,
        issued_at=issued_at,
        ipfs_json_cid=ipfs_result.json_cid,
        ipfs_pdf_cid=ipfs_result.pdf_cid,
        ipfs_provider=ipfs_result.provider,
        registry_tx_hash=chain_result.tx_hash,
        registry_block=chain_result.block_number,
        contract_address=chain_result.contract_address,
        registered_at=chain_result.registered_at,
    )

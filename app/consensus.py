"""
Motor de Consenso Granular — Camada 5 da Fortaleza Lógica

Compara geração normalizada (kWh/kWp) da planta alvo com vizinhas
dentro de um raio geoespacial. Divergência > X% da mediana → flag.

Modelo:
  1. Encontra plantas vizinhas dentro de RADIUS_KM (default 5km)
  2. Busca telemetria recente das vizinhas (±TIME_WINDOW)
  3. Normaliza: ratio = energy_kwh / capacity_kw
  4. Calcula mediana dos ratios das vizinhas
  5. Calcula desvio: |ratio_alvo - mediana| / mediana × 100
  6. Se desvio > DEVIATION_THRESHOLD_PCT → consensus_pass = False

Distância:
  - Produção (PostgreSQL + PostGIS): ST_DWithin para performance
  - Fallback (SQLite / sem PostGIS): Haversine em Python

Requisitos mínimos:
  - MIN_NEIGHBORS = 2 (precisa de pelo menos 2 vizinhas para consenso)
  - Se < MIN_NEIGHBORS → consensus_pass = None (inconclusivo)
"""
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.models import Plant, Telemetry


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
RADIUS_KM = 5.0                    # Raio de busca de vizinhas
DEVIATION_THRESHOLD_PCT = 30.0     # Desvio máximo da mediana (%)
TIME_WINDOW_MINUTES = 30           # Janela temporal para telemetria vizinha (±30min)
MIN_NEIGHBORS = 2                  # Mínimo de vizinhas para consenso válido
EARTH_RADIUS_KM = 6371.0          # Raio da Terra (km)


# ---------------------------------------------------------------------------
# Resultado do consenso
# ---------------------------------------------------------------------------

@dataclass
class NeighborReading:
    """Leitura de uma planta vizinha."""
    plant_id: uuid.UUID
    plant_name: str
    distance_km: float
    capacity_kw: float
    energy_kwh: float
    normalized_ratio: float   # energy_kwh / capacity_kw
    telemetry_time: datetime


@dataclass
class ConsensusResult:
    """Resultado da validação por consenso geoespacial."""
    consensus_pass: Optional[bool]     # True=ok, False=divergente, None=inconclusivo
    deviation_pct: Optional[float]     # Desvio da mediana (%)
    median_ratio: Optional[float]      # Mediana dos ratios das vizinhas
    plant_ratio: float                 # Ratio da planta alvo
    neighbor_count: int                # Quantidade de vizinhas encontradas
    neighbors_used: int                # Vizinhas com telemetria na janela
    radius_km: float                   # Raio usado
    threshold_pct: float               # Threshold usado
    neighbors: List[NeighborReading] = field(default_factory=list)
    reason: str = ""                   # Explicação do resultado


# ---------------------------------------------------------------------------
# Haversine — distância entre dois pontos GPS (km)
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Calcula distância entre dois pontos GPS em km (fórmula de Haversine).
    Funciona sem PostGIS — fallback universal.
    """
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlng / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return EARTH_RADIUS_KM * c


# ---------------------------------------------------------------------------
# Busca de vizinhas
# ---------------------------------------------------------------------------

def find_neighbors(
    db: Session,
    plant: Plant,
    radius_km: float = RADIUS_KM,
) -> List[Tuple[Plant, float]]:
    """
    Encontra plantas ativas dentro de radius_km da planta alvo.

    Em produção com PostGIS:
      SELECT * FROM plants
      WHERE ST_DWithin(geom, ST_MakePoint(lng, lat)::geography, radius_m)
        AND plant_id != target_id AND status = 'active'

    Fallback (SQLite / sem PostGIS):
      Query todas as plants ativas + filtro Haversine em Python.

    Returns:
        Lista de (Plant, distance_km) ordenada por distância
    """
    target_lat = float(plant.lat)
    target_lng = float(plant.lng)

    # Tentar PostGIS primeiro
    try:
        return _find_neighbors_postgis(db, plant, target_lat, target_lng, radius_km)
    except Exception:
        pass

    # Fallback: Haversine em Python
    return _find_neighbors_haversine(db, plant, target_lat, target_lng, radius_km)


def _find_neighbors_postgis(
    db: Session, plant: Plant, lat: float, lng: float, radius_km: float,
) -> List[Tuple[Plant, float]]:
    """PostGIS path: usa ST_DWithin + ST_Distance para performance."""
    from sqlalchemy import text

    radius_m = radius_km * 1000.0
    sql = text("""
        SELECT plant_id,
               ST_Distance(
                   ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography,
                   ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography
               ) / 1000.0 AS dist_km
        FROM plants
        WHERE plant_id != :pid
          AND status = 'active'
          AND ST_DWithin(
              ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography,
              ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
              :radius_m
          )
        ORDER BY dist_km
    """)

    rows = db.execute(sql, {
        "lng": lng, "lat": lat,
        "pid": str(plant.plant_id),
        "radius_m": radius_m,
    }).fetchall()

    result = []
    for row in rows:
        p = db.query(Plant).filter(Plant.plant_id == row.plant_id).first()
        if p:
            result.append((p, row.dist_km))
    return result


def _find_neighbors_haversine(
    db: Session, plant: Plant, lat: float, lng: float, radius_km: float,
) -> List[Tuple[Plant, float]]:
    """Fallback: query todas as plantas + filtro Haversine em Python."""
    all_plants = (
        db.query(Plant)
        .filter(Plant.plant_id != plant.plant_id)
        .filter(Plant.status == "active")
        .all()
    )

    neighbors = []
    for p in all_plants:
        dist = haversine_km(lat, lng, float(p.lat), float(p.lng))
        if dist <= radius_km:
            neighbors.append((p, round(dist, 4)))

    neighbors.sort(key=lambda x: x[1])
    return neighbors


# ---------------------------------------------------------------------------
# Busca de telemetria recente das vizinhas
# ---------------------------------------------------------------------------

def get_neighbor_readings(
    db: Session,
    neighbors: List[Tuple[Plant, float]],
    reference_time: datetime,
    time_window_minutes: int = TIME_WINDOW_MINUTES,
) -> List[NeighborReading]:
    """
    Busca a telemetria mais recente de cada vizinha dentro da janela temporal.

    Args:
        neighbors: Lista de (Plant, distance_km)
        reference_time: Timestamp de referência (da telemetria alvo)
        time_window_minutes: Janela ± em minutos

    Returns:
        Lista de NeighborReading com dados normalizados
    """
    window_start = reference_time - timedelta(minutes=time_window_minutes)
    window_end = reference_time + timedelta(minutes=time_window_minutes)

    readings = []
    for plant, dist_km in neighbors:
        # Buscar telemetria mais recente da vizinha dentro da janela
        telemetry = (
            db.query(Telemetry)
            .filter(Telemetry.plant_id == plant.plant_id)
            .filter(Telemetry.time >= window_start)
            .filter(Telemetry.time <= window_end)
            .order_by(Telemetry.time.desc())
            .first()
        )

        if telemetry and float(plant.capacity_kw) > 0:
            energy = float(telemetry.energy_kwh)
            capacity = float(plant.capacity_kw)
            ratio = energy / capacity

            readings.append(NeighborReading(
                plant_id=plant.plant_id,
                plant_name=plant.name,
                distance_km=dist_km,
                capacity_kw=capacity,
                energy_kwh=energy,
                normalized_ratio=round(ratio, 6),
                telemetry_time=telemetry.time,
            ))

    return readings


# ---------------------------------------------------------------------------
# Cálculo de mediana
# ---------------------------------------------------------------------------

def _median(values: List[float]) -> float:
    """Calcula mediana de uma lista de valores."""
    sorted_v = sorted(values)
    n = len(sorted_v)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2 == 0:
        return (sorted_v[mid - 1] + sorted_v[mid]) / 2.0
    return sorted_v[mid]


# ---------------------------------------------------------------------------
# Motor de consenso
# ---------------------------------------------------------------------------

def validate_consensus(
    db: Session,
    plant: Plant,
    energy_kwh: float,
    reference_time: datetime,
    radius_km: float = RADIUS_KM,
    deviation_threshold_pct: float = DEVIATION_THRESHOLD_PCT,
    time_window_minutes: int = TIME_WINDOW_MINUTES,
    min_neighbors: int = MIN_NEIGHBORS,
) -> ConsensusResult:
    """
    Camada 5: Consenso Granular Geoespacial.

    Compara a geração normalizada (kWh/kWp) da planta alvo com a
    mediana das vizinhas dentro do raio.

    Decisão:
      - neighbors_used >= min_neighbors → calcula desvio
        - desvio <= threshold → consensus_pass = True
        - desvio > threshold → consensus_pass = False
      - neighbors_used < min_neighbors → consensus_pass = None (inconclusivo)

    Args:
        db: Sessão do banco
        plant: Planta alvo
        energy_kwh: Energia reportada pela planta alvo
        reference_time: Timestamp da telemetria
        radius_km: Raio de busca (default 5km)
        deviation_threshold_pct: Desvio máximo permitido (default 30%)
        time_window_minutes: Janela temporal (default ±30min)
        min_neighbors: Mínimo de vizinhas necessárias (default 2)

    Returns:
        ConsensusResult com todos os dados da validação
    """
    capacity_kw = float(plant.capacity_kw)

    # Ratio da planta alvo
    if capacity_kw > 0:
        plant_ratio = energy_kwh / capacity_kw
    else:
        plant_ratio = 0.0

    # 1. Encontrar vizinhas
    neighbors = find_neighbors(db, plant, radius_km)

    # 2. Buscar telemetria recente das vizinhas
    readings = get_neighbor_readings(db, neighbors, reference_time, time_window_minutes)

    neighbors_used = len(readings)

    # 3. Verificar se há vizinhas suficientes
    if neighbors_used < min_neighbors:
        return ConsensusResult(
            consensus_pass=None,
            deviation_pct=None,
            median_ratio=None,
            plant_ratio=round(plant_ratio, 6),
            neighbor_count=len(neighbors),
            neighbors_used=neighbors_used,
            radius_km=radius_km,
            threshold_pct=deviation_threshold_pct,
            neighbors=readings,
            reason=(
                f"Inconclusivo — apenas {neighbors_used} vizinha(s) com telemetria "
                f"(mínimo {min_neighbors}) dentro de {radius_km}km"
            ),
        )

    # 4. Calcular mediana dos ratios das vizinhas
    neighbor_ratios = [r.normalized_ratio for r in readings]
    median_ratio = _median(neighbor_ratios)

    # 5. Calcular desvio percentual
    if median_ratio > 0:
        deviation_pct = abs(plant_ratio - median_ratio) / median_ratio * 100.0
    elif plant_ratio > 0:
        # Mediana zero mas planta reportou geração → desvio infinito
        deviation_pct = 100.0
    else:
        # Ambos zero → sem desvio
        deviation_pct = 0.0

    deviation_pct = round(deviation_pct, 2)

    # 6. Decidir
    consensus_pass = deviation_pct <= deviation_threshold_pct

    if consensus_pass:
        reason = (
            f"Consenso ok — desvio {deviation_pct:.1f}% dentro do limite "
            f"{deviation_threshold_pct:.0f}% (mediana vizinhas: {median_ratio:.4f} kWh/kWp, "
            f"planta: {plant_ratio:.4f} kWh/kWp, {neighbors_used} vizinhas)"
        )
    else:
        reason = (
            f"DIVERGÊNCIA — desvio {deviation_pct:.1f}% excede limite "
            f"{deviation_threshold_pct:.0f}% (mediana vizinhas: {median_ratio:.4f} kWh/kWp, "
            f"planta: {plant_ratio:.4f} kWh/kWp, {neighbors_used} vizinhas em {radius_km}km)"
        )

    return ConsensusResult(
        consensus_pass=consensus_pass,
        deviation_pct=deviation_pct,
        median_ratio=round(median_ratio, 6),
        plant_ratio=round(plant_ratio, 6),
        neighbor_count=len(neighbors),
        neighbors_used=neighbors_used,
        radius_km=radius_km,
        threshold_pct=deviation_threshold_pct,
        neighbors=readings,
        reason=reason,
    )

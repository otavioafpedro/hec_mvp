"""
Módulo de Física Teórica — Camada 3 da Fortaleza Lógica (Patente 33)

Projeta curva solar teórica para GPS/horário e bloqueia se geração
reportada exceder o máximo fisicamente possível.

Modelo:
  1. Calcula posição solar (elevação) para lat/lng/timestamp
  2. Estima irradiância clear-sky (GHI) via modelo Ineichen/pvlib
  3. Computa potência máxima teórica: capacity_kw × (GHI / 1000)
  4. Converte para energia na janela temporal (default 1h)
  5. Aplica margem de segurança (+15%) para condições excepcionais
  6. physics_pass = (energy_kwh <= theoretical_max_kwh)

Usa pvlib quando disponível; fallback analítico (Kasten-Young + Ineichen)
quando pvlib não está instalado.
"""
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import numpy as np

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
STC_IRRADIANCE_WM2 = 1000.0   # Irradiância padrão STC (W/m²)
SAFETY_MARGIN = 1.15           # +15% margem para edge effects, reflexão, etc.
DEFAULT_INTERVAL_HOURS = 1.0   # Janela temporal padrão para cálculo de energia
NIGHT_THRESHOLD_DEG = 0.0      # Sol abaixo do horizonte → geração zero


@dataclass
class PhysicsResult:
    """Resultado da validação física para um ponto de telemetria."""
    theoretical_max_kwh: float   # Máximo teórico de energia no intervalo
    theoretical_max_kw: float    # Potência máxima teórica instantânea
    ghi_clear_sky_wm2: float     # Irradiância clear-sky estimada (W/m²)
    solar_elevation_deg: float   # Elevação solar (graus)
    physics_pass: bool           # True se energy_kwh <= theoretical_max_kwh
    reported_kwh: float          # Energia reportada pelo inversor
    capacity_kw: float           # Capacidade instalada da planta
    interval_hours: float        # Janela temporal usada
    method: str                  # "pvlib" ou "analytical"


# ---------------------------------------------------------------------------
# 1. Posição solar — equações analíticas (Spencer, 1971)
# ---------------------------------------------------------------------------

def _day_of_year(dt: datetime) -> int:
    return dt.timetuple().tm_yday


def _solar_declination_deg(day_of_year: int) -> float:
    """Declinação solar (graus) via equação de Spencer."""
    B = math.radians((360.0 / 365.0) * (day_of_year - 81))
    decl = 23.45 * math.sin(B)
    return decl


def _equation_of_time_min(day_of_year: int) -> float:
    """Equação do tempo (minutos) — correção para hora solar verdadeira."""
    B = math.radians((360.0 / 365.0) * (day_of_year - 81))
    eot = 9.87 * math.sin(2 * B) - 7.53 * math.cos(B) - 1.5 * math.sin(B)
    return eot


def _solar_elevation_deg(lat: float, lng: float, dt: datetime) -> float:
    """
    Calcula elevação solar (graus acima do horizonte) para lat/lng/datetime UTC.
    Usa equações analíticas padrão (Spencer / NOAA).
    """
    doy = _day_of_year(dt)
    decl = math.radians(_solar_declination_deg(doy))
    lat_rad = math.radians(lat)

    # Hora solar verdadeira
    eot = _equation_of_time_min(doy)
    # Hora UTC decimal
    utc_hour = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
    # Hora solar aparente (corrigida por longitude e EoT)
    solar_time = utc_hour + lng / 15.0 + eot / 60.0
    # Ângulo horário (graus, 0 = meio-dia solar)
    hour_angle = math.radians(15.0 * (solar_time - 12.0))

    # Elevação solar
    sin_elev = (
        math.sin(lat_rad) * math.sin(decl)
        + math.cos(lat_rad) * math.cos(decl) * math.cos(hour_angle)
    )
    sin_elev = max(-1.0, min(1.0, sin_elev))  # Clamp
    elevation = math.degrees(math.asin(sin_elev))

    return elevation


# ---------------------------------------------------------------------------
# 2. Irradiância clear-sky — modelo simplificado Ineichen/Perez
# ---------------------------------------------------------------------------

def _clear_sky_ghi_wm2(solar_elevation_deg: float, altitude_m: float = 0.0) -> float:
    """
    Estima GHI clear-sky (W/m²) usando modelo Kasten-Young para massa de ar
    e Ineichen simplificado para irradiância.

    Se sol abaixo do horizonte, retorna 0.
    """
    if solar_elevation_deg <= NIGHT_THRESHOLD_DEG:
        return 0.0

    # Massa de ar (Kasten-Young, 1989)
    zenith = 90.0 - solar_elevation_deg
    zenith_rad = math.radians(zenith)

    if zenith >= 89.9:
        return 0.0

    air_mass = 1.0 / (math.cos(zenith_rad) + 0.50572 * (96.07995 - zenith) ** (-1.6364))

    # Ineichen clear-sky simplificado
    # GHI ≈ 1366 × 0.7^(AM^0.678) × sin(elevation)
    # Ajuste por altitude (≈ +10% por km acima do nível do mar)
    altitude_factor = 1.0 + 0.1 * (altitude_m / 1000.0)
    ghi = 1366.0 * (0.7 ** (air_mass ** 0.678)) * math.sin(math.radians(solar_elevation_deg))
    ghi *= altitude_factor

    return max(0.0, ghi)


# ---------------------------------------------------------------------------
# 3. Validação física — pvlib (primário) + analytical (fallback)
# ---------------------------------------------------------------------------

def _try_pvlib(lat: float, lng: float, timestamp: datetime, altitude_m: float = 0.0):
    """
    Tenta usar pvlib para cálculo preciso de posição solar e clear-sky GHI.
    Retorna (elevation_deg, ghi_wm2, "pvlib") ou None se pvlib indisponível.
    """
    try:
        import pvlib
        import pandas as pd

        # pvlib exige DatetimeIndex com timezone
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        times = pd.DatetimeIndex([timestamp])
        location = pvlib.location.Location(lat, lng, altitude=altitude_m)

        # Posição solar
        solpos = location.get_solarposition(times)
        elevation = float(solpos["apparent_elevation"].iloc[0])

        # Clear-sky GHI (modelo Ineichen)
        cs = location.get_clearsky(times, model="ineichen")
        ghi = float(cs["ghi"].iloc[0])

        return elevation, ghi, "pvlib"

    except (ImportError, Exception):
        return None


def _analytical_solar(lat: float, lng: float, timestamp: datetime, altitude_m: float = 0.0):
    """Fallback analítico para posição solar e clear-sky GHI."""
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    elevation = _solar_elevation_deg(lat, lng, timestamp)
    ghi = _clear_sky_ghi_wm2(elevation, altitude_m)

    return elevation, ghi, "analytical"


def compute_theoretical_max(
    lat: float,
    lng: float,
    capacity_kw: float,
    timestamp: datetime,
    reported_kwh: float,
    interval_hours: float = DEFAULT_INTERVAL_HOURS,
    altitude_m: float = 0.0,
    safety_margin: float = SAFETY_MARGIN,
    force_analytical: bool = False,
) -> PhysicsResult:
    """
    Calcula geração máxima teórica e valida contra energia reportada.

    Modelo:
      max_power_kw = capacity_kw × (GHI / 1000) × safety_margin
      max_energy_kwh = max_power_kw × interval_hours
      physics_pass = (reported_kwh <= max_energy_kwh)

    Args:
        lat: Latitude da planta (-90 a +90)
        lng: Longitude da planta (-180 a +180)
        capacity_kw: Capacidade instalada em kWp
        timestamp: Datetime UTC da leitura
        reported_kwh: Energia reportada pelo inversor (kWh)
        interval_hours: Janela temporal em horas (default 1h)
        altitude_m: Altitude em metros (default 0)
        safety_margin: Margem de segurança (default 1.15 = +15%)
        force_analytical: Se True, ignora pvlib e usa modelo analítico

    Returns:
        PhysicsResult com todos os dados da validação
    """
    # Tentar pvlib primeiro (se disponível e não forçado analítico)
    result = None
    if not force_analytical:
        result = _try_pvlib(lat, lng, timestamp, altitude_m)

    if result is None:
        result = _analytical_solar(lat, lng, timestamp, altitude_m)

    elevation, ghi, method = result

    # Potência máxima teórica (kW)
    if ghi <= 0 or elevation <= NIGHT_THRESHOLD_DEG:
        theoretical_max_kw = 0.0
    else:
        theoretical_max_kw = capacity_kw * (ghi / STC_IRRADIANCE_WM2) * safety_margin

    # Energia máxima teórica no intervalo (kWh)
    theoretical_max_kwh = theoretical_max_kw * interval_hours

    # Round for consistent comparisons (avoid floating-point boundary issues)
    theoretical_max_kwh = round(theoretical_max_kwh, 4)
    theoretical_max_kw = round(theoretical_max_kw, 4)
    ghi = round(ghi, 2)
    elevation = round(elevation, 2)

    # Validação
    physics_pass = reported_kwh <= theoretical_max_kwh

    return PhysicsResult(
        theoretical_max_kwh=theoretical_max_kwh,
        theoretical_max_kw=theoretical_max_kw,
        ghi_clear_sky_wm2=ghi,
        solar_elevation_deg=elevation,
        physics_pass=physics_pass,
        reported_kwh=reported_kwh,
        capacity_kw=capacity_kw,
        interval_hours=interval_hours,
        method=method,
    )

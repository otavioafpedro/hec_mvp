"""
Serviço Satellite Fetcher — Camada 4 da Fortaleza Lógica (Validação Orbital)

Busca irradiância medida por satélite (INPE GOES-16 / Copernicus CAMS) para
um dado GPS + timestamp, e cruza com a geração reportada pelo inversor.

Regra de Cross-Validation:
  Se irradiância_satélite é BAIXA mas geração é ALTA → flag (greenwashing/fraude)

Providers (plugável):
  - MockProvider: dados sintéticos para dev/test (padrão)
  - INPEProvider: API INPE GOES-16 (produção Brasil) [stub]
  - CAMSProvider: Copernicus Atmosphere Monitoring Service [stub]

Modelo de decisão:
  1. Busca irradiância GHI do satélite para lat/lng/timestamp
  2. Calcula geração máxima baseada em irradiância real do satélite:
     sat_max_kwh = capacity_kw × (sat_ghi / 1000) × safety_margin × hours
  3. Se energy_kwh > sat_max_kwh → satellite_pass = False
  4. Se sat_ghi < LOW_IRRADIANCE_THRESHOLD e energy_kwh > sat_max_kwh → flag severa

Diferencial vs Camada 3 (physics):
  - Camada 3 usa céu limpo teórico (melhor caso)
  - Camada 4 usa irradiância REAL do satélite (nuvens, chuva, fumaça incluídos)
  - Camada 4 é mais restritiva: pega fraude que passa na Camada 3
"""
import math
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
STC_IRRADIANCE_WM2 = 1000.0          # Condição padrão de teste (W/m²)
SATELLITE_SAFETY_MARGIN = 1.20        # +20% margem (erro de satélite + micro-clima)
LOW_IRRADIANCE_THRESHOLD_WM2 = 150.0  # Abaixo = "irradiância baixa" (nublado forte)
DEFAULT_INTERVAL_HOURS = 1.0


# ---------------------------------------------------------------------------
# Resultado da validação satélite
# ---------------------------------------------------------------------------

@dataclass
class SatelliteReading:
    """Leitura de irradiância do satélite para um ponto GPS + timestamp."""
    ghi_wm2: float              # Irradiância GHI medida pelo satélite (W/m²)
    source: str                 # Provider usado ("mock", "inpe_goes16", "cams")
    timestamp: datetime         # Timestamp da leitura
    lat: float
    lng: float
    cloud_cover_pct: float      # Cobertura de nuvens estimada (0-100%)
    confidence: float           # Confiança da medição (0-1)
    raw_data: Optional[dict] = None  # Dados brutos do provider


@dataclass
class SatelliteValidationResult:
    """Resultado completo da validação por satélite."""
    satellite_ghi_wm2: float      # GHI medido pelo satélite
    satellite_source: str         # Provider ("mock", "inpe_goes16", etc.)
    satellite_max_kwh: float      # Geração máxima baseada na irradiância real
    satellite_pass: bool          # True se energy <= satellite_max
    cloud_cover_pct: float        # Cobertura de nuvens (0-100)
    low_irradiance: bool          # True se GHI < LOW_IRRADIANCE_THRESHOLD
    high_generation_low_sun: bool # True se geração alta + irradiância baixa (flag severa)
    reported_kwh: float
    confidence: float


# ---------------------------------------------------------------------------
# Interface do Provider
# ---------------------------------------------------------------------------

class SatelliteProvider(ABC):
    """Interface base para provedores de dados de satélite."""

    @abstractmethod
    def fetch_irradiance(
        self, lat: float, lng: float, timestamp: datetime,
    ) -> SatelliteReading:
        """Busca irradiância GHI do satélite para lat/lng/timestamp."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...


# ---------------------------------------------------------------------------
# Mock Provider — Dados sintéticos para dev/test
# ---------------------------------------------------------------------------

class MockSatelliteProvider(SatelliteProvider):
    """
    Provider mock que simula dados de satélite com modelo solar realista.

    Suporta dois modos:
      1. Auto (default): calcula GHI baseado em posição solar + nuvem simulada
      2. Fixed: retorna valores fixos injetados (para testes determinísticos)
    """

    def __init__(
        self,
        fixed_ghi_wm2: Optional[float] = None,
        fixed_cloud_cover_pct: Optional[float] = None,
        add_noise: bool = True,
    ):
        self._fixed_ghi = fixed_ghi_wm2
        self._fixed_cloud = fixed_cloud_cover_pct
        self._add_noise = add_noise

    @property
    def name(self) -> str:
        return "mock"

    def fetch_irradiance(
        self, lat: float, lng: float, timestamp: datetime,
    ) -> SatelliteReading:
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        if self._fixed_ghi is not None:
            ghi = self._fixed_ghi
            cloud = self._fixed_cloud if self._fixed_cloud is not None else 0.0
        else:
            ghi, cloud = self._simulate_satellite(lat, lng, timestamp)

        return SatelliteReading(
            ghi_wm2=round(max(0.0, ghi), 2),
            source="mock",
            timestamp=timestamp,
            lat=lat,
            lng=lng,
            cloud_cover_pct=round(cloud, 1),
            confidence=0.85,
            raw_data={"provider": "mock", "mode": "fixed" if self._fixed_ghi is not None else "auto"},
        )

    def _simulate_satellite(self, lat: float, lng: float, timestamp: datetime):
        """Simula GHI de satélite com clear-sky atenuado por nuvens."""
        from app.physics import _solar_elevation_deg, _clear_sky_ghi_wm2

        elevation = _solar_elevation_deg(lat, lng, timestamp)
        clear_sky_ghi = _clear_sky_ghi_wm2(elevation)

        # Simular cobertura de nuvens (determinístico baseado em hora)
        hour = timestamp.hour + timestamp.minute / 60.0
        # Padrão: mais nuvens de manhã cedo e fim de tarde
        cloud_base = 20.0 + 15.0 * math.sin(math.pi * (hour - 6) / 12)
        cloud_cover = max(0.0, min(100.0, cloud_base))

        # Atenuar GHI por nuvens
        cloud_factor = 1.0 - (cloud_cover / 100.0) * 0.75  # nuvem 100% → 25% do GHI
        ghi = clear_sky_ghi * cloud_factor

        # Noise controlado
        if self._add_noise and ghi > 0:
            noise = random.gauss(0, ghi * 0.03)  # ±3%
            ghi = max(0.0, ghi + noise)

        return ghi, cloud_cover


# ---------------------------------------------------------------------------
# INPE GOES-16 Provider (stub para produção)
# ---------------------------------------------------------------------------

class INPESatelliteProvider(SatelliteProvider):
    """
    Provider para dados INPE GOES-16 — satélite geoestacionário brasileiro.
    Endpoint: http://ftp.cptec.inpe.br/goes/ (NetCDF)
    Resolução: ~2km, atualização a cada 10-15min

    TODO: Implementar fetch real via API INPE quando em produção.
    """

    def __init__(self, api_url: str = "http://ftp.cptec.inpe.br/goes/"):
        self._api_url = api_url

    @property
    def name(self) -> str:
        return "inpe_goes16"

    def fetch_irradiance(
        self, lat: float, lng: float, timestamp: datetime,
    ) -> SatelliteReading:
        # Em produção: fetch do NetCDF via INPE, interpolar para lat/lng
        # Por ora: fallback para mock com tag do provider
        mock = MockSatelliteProvider(add_noise=True)
        reading = mock.fetch_irradiance(lat, lng, timestamp)
        # Sobrescrever source para indicar que seria INPE
        return SatelliteReading(
            ghi_wm2=reading.ghi_wm2,
            source="inpe_goes16",
            timestamp=reading.timestamp,
            lat=lat, lng=lng,
            cloud_cover_pct=reading.cloud_cover_pct,
            confidence=0.92,  # GOES-16 tem boa resolução para Brasil
            raw_data={"provider": "inpe_goes16", "status": "stub_mock_fallback"},
        )


# ---------------------------------------------------------------------------
# CAMS / Copernicus Provider (stub para produção)
# ---------------------------------------------------------------------------

class CAMSSatelliteProvider(SatelliteProvider):
    """
    Provider Copernicus Atmosphere Monitoring Service (CAMS).
    API: https://ads.atmosphere.copernicus.eu/
    Cobertura: global, resolução ~40km, dados horários

    TODO: Implementar fetch real via CAMS API quando em produção.
    """

    @property
    def name(self) -> str:
        return "cams_copernicus"

    def fetch_irradiance(
        self, lat: float, lng: float, timestamp: datetime,
    ) -> SatelliteReading:
        mock = MockSatelliteProvider(add_noise=True)
        reading = mock.fetch_irradiance(lat, lng, timestamp)
        return SatelliteReading(
            ghi_wm2=reading.ghi_wm2,
            source="cams_copernicus",
            timestamp=reading.timestamp,
            lat=lat, lng=lng,
            cloud_cover_pct=reading.cloud_cover_pct,
            confidence=0.88,
            raw_data={"provider": "cams_copernicus", "status": "stub_mock_fallback"},
        )


# ---------------------------------------------------------------------------
# Motor de validação por satélite
# ---------------------------------------------------------------------------

# Provider global injetável (padrão: Mock em dev, INPE em prod)
_active_provider: SatelliteProvider = MockSatelliteProvider()


def set_satellite_provider(provider: SatelliteProvider) -> None:
    """Injeta provider de satélite (para testes ou troca em runtime)."""
    global _active_provider
    _active_provider = provider


def get_satellite_provider() -> SatelliteProvider:
    """Retorna o provider ativo."""
    return _active_provider


def reset_satellite_provider() -> None:
    """Restaura provider padrão (Mock)."""
    global _active_provider
    _active_provider = MockSatelliteProvider()


def validate_satellite(
    lat: float,
    lng: float,
    capacity_kw: float,
    timestamp: datetime,
    reported_kwh: float,
    interval_hours: float = DEFAULT_INTERVAL_HOURS,
    safety_margin: float = SATELLITE_SAFETY_MARGIN,
    provider: Optional[SatelliteProvider] = None,
) -> SatelliteValidationResult:
    """
    Camada 4: Cross-validation com irradiância de satélite.

    1. Busca GHI real do satélite para lat/lng/timestamp
    2. Calcula geração máxima real:
       sat_max_kwh = capacity_kw × (sat_ghi / 1000) × safety_margin × hours
    3. Compara com geração reportada

    Flags:
      - satellite_pass = False  →  geração excede máximo baseado em satélite
      - low_irradiance = True   →  GHI < 150 W/m² (nublado forte)
      - high_generation_low_sun →  flag SEVERA: geração alta com irradiância baixa

    Args:
        lat, lng: Coordenadas GPS da planta
        capacity_kw: Capacidade instalada (kWp)
        timestamp: Datetime UTC da leitura
        reported_kwh: Energia reportada pelo inversor
        interval_hours: Janela temporal (default 1h)
        safety_margin: Margem de segurança (default 1.20 = +20%)
        provider: Provider específico (None = usa provider global)

    Returns:
        SatelliteValidationResult
    """
    active = provider or _active_provider

    # 1. Fetch irradiância do satélite
    reading = active.fetch_irradiance(lat, lng, timestamp)
    sat_ghi = reading.ghi_wm2

    # 2. Calcular geração máxima baseada na irradiância real
    if sat_ghi <= 0:
        satellite_max_kwh = 0.0
    else:
        satellite_max_kwh = capacity_kw * (sat_ghi / STC_IRRADIANCE_WM2) * safety_margin * interval_hours
    satellite_max_kwh = round(satellite_max_kwh, 4)

    # 3. Validar
    satellite_pass = reported_kwh <= satellite_max_kwh
    low_irradiance = sat_ghi < LOW_IRRADIANCE_THRESHOLD_WM2

    # Flag severa: geração alta com irradiância baixa
    # "Alta" = acima de 10% da capacidade nominal por hora
    high_gen_threshold = capacity_kw * 0.10 * interval_hours  # 10% da capacidade
    high_generation_low_sun = low_irradiance and (reported_kwh > high_gen_threshold)

    return SatelliteValidationResult(
        satellite_ghi_wm2=round(sat_ghi, 2),
        satellite_source=reading.source,
        satellite_max_kwh=satellite_max_kwh,
        satellite_pass=satellite_pass,
        cloud_cover_pct=reading.cloud_cover_pct,
        low_irradiance=low_irradiance,
        high_generation_low_sun=high_generation_low_sun,
        reported_kwh=reported_kwh,
        confidence=reading.confidence,
    )

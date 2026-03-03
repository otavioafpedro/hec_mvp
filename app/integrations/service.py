import asyncio
from datetime import datetime, timezone

from app.config import settings
from app.integrations import copernicus, electricity_maps, inmet, openweather, solcast


async def get_integrations_status() -> dict:
    checks = await asyncio.gather(
        solcast.test_connection(),
        copernicus.test_connection(),
        inmet.test_connection(),
        openweather.test_connection(),
        electricity_maps.test_connection(),
    )

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": {
            "s1_solcast": checks[0],
            "s2_satellite": checks[1],
            "s3_weather_inmet": checks[2],
            "s3_weather_openweather": checks[3],
            "esg_electricity_maps": checks[4],
        },
    }


async def collect_site_context(lat: float, lng: float) -> dict:
    s1_task = asyncio.create_task(solcast.get_generation_estimate(lat=lat, lng=lng))
    s2_task = asyncio.create_task(copernicus.get_solar_radiation(lat=lat, lng=lng))
    s3_inmet_task = asyncio.create_task(inmet.get_weather_data(station_id="A701"))
    s3_owm_task = asyncio.create_task(openweather.get_weather_data(lat=lat, lng=lng))
    esg_task = asyncio.create_task(
        electricity_maps.get_carbon_intensity(zone=settings.DEFAULT_GRID_ZONE)
    )

    s1 = await s1_task
    s2 = await s2_task
    s3_inmet = await s3_inmet_task
    s3_openweather = await s3_owm_task
    esg = await esg_task

    weather = s3_inmet if s3_inmet.get("status") == "ok" else s3_openweather

    return {
        "generation": s1,
        "satellite": s2,
        "weather": weather,
        "weather_fallback": s3_openweather,
        "carbon": esg,
    }

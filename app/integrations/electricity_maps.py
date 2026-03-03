from datetime import datetime, timezone

import httpx

from app.config import settings


async def get_carbon_intensity(zone: str | None = None) -> dict:
    if not settings.ELECTRICITY_MAPS_KEY:
        return {"status": "no_key", "carbonIntensity": 0.0, "source": "electricity_maps"}

    zone_value = zone or settings.DEFAULT_GRID_ZONE
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{settings.ELECTRICITY_MAPS_URL}/carbon-intensity/latest",
                params={"zone": zone_value},
                headers={"auth-token": settings.ELECTRICITY_MAPS_KEY},
            )
        if response.status_code != 200:
            return {
                "status": f"error_{response.status_code}",
                "carbonIntensity": 0.0,
                "source": "electricity_maps",
                "zone": zone_value,
            }

        payload = response.json()
        return {
            "status": "ok",
            "zone": zone_value,
            "carbonIntensity": float(payload.get("carbonIntensity", 0.0) or 0.0),
            "fossilFuelPercentage": payload.get("fossilFuelPercentage"),
            "source": "electricity_maps",
        }
    except Exception as exc:  # pragma: no cover - runtime defensive path
        return {
            "status": f"error:{exc.__class__.__name__}",
            "carbonIntensity": 0.0,
            "source": "electricity_maps",
            "zone": zone_value,
        }


async def test_connection() -> dict:
    if not settings.ELECTRICITY_MAPS_KEY:
        return {"status": "not_configured", "configured": False}

    result = await get_carbon_intensity()
    return {
        "status": result.get("status"),
        "configured": True,
        "source": "electricity_maps",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

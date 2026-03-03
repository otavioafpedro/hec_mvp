from datetime import datetime, timezone

import httpx

from app.config import settings


async def get_weather_data(lat: float, lng: float) -> dict:
    if not settings.OPENWEATHER_API_KEY:
        return {"status": "no_key", "source": "openweather"}

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{settings.OPENWEATHER_BASE_URL}/onecall",
                params={
                    "lat": lat,
                    "lon": lng,
                    "appid": settings.OPENWEATHER_API_KEY,
                    "units": "metric",
                },
            )
        if response.status_code != 200:
            return {"status": f"error_{response.status_code}", "source": "openweather"}

        payload = response.json()
        current = payload.get("current", {})
        return {
            "status": "ok",
            "temperature": _as_float(current.get("temp")),
            "humidity": _as_float(current.get("humidity")),
            "clouds": _as_float(current.get("clouds")),
            "uvi": _as_float(current.get("uvi")),
            "wind_speed": _as_float(current.get("wind_speed")),
            "pressure": _as_float(current.get("pressure")),
            "source": "openweather",
        }
    except Exception as exc:  # pragma: no cover - runtime defensive path
        return {"status": f"error:{exc.__class__.__name__}", "source": "openweather"}


def _as_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def test_connection() -> dict:
    if not settings.OPENWEATHER_API_KEY:
        return {"status": "not_configured", "configured": False}

    result = await get_weather_data(lat=-23.5505, lng=-46.6333)
    return {
        "status": result.get("status"),
        "configured": True,
        "source": "openweather",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

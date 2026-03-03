from datetime import datetime, timezone

import httpx

from app.config import settings


def _today_yyyymmdd() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


async def get_solar_radiation(lat: float, lng: float) -> dict:
    # Copernicus OAuth flow is still pending in this project.
    # Keep the proven fallback path from SOA gateway (NASA POWER).
    date_value = _today_yyyymmdd()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{settings.NASA_POWER_BASE_URL}/temporal/hourly/point",
                params={
                    "parameters": "ALLSKY_SFC_SW_DWN",
                    "community": "RE",
                    "longitude": lng,
                    "latitude": lat,
                    "start": date_value,
                    "end": date_value,
                    "format": "JSON",
                },
            )
        if response.status_code != 200:
            return {"status": f"error_{response.status_code}", "ghi_wm2": 0.0, "source": "nasa_power"}

        payload = response.json()
        params = payload.get("properties", {}).get("parameter", {})
        series = params.get("ALLSKY_SFC_SW_DWN", {})
        latest = list(series.values())[-1] if series else 0.0
        return {
            "status": "ok",
            "ghi_wm2": float(latest or 0.0),
            "source": "nasa_power",
        }
    except Exception as exc:  # pragma: no cover - runtime defensive path
        return {"status": f"error:{exc.__class__.__name__}", "ghi_wm2": 0.0, "source": "nasa_power"}


async def test_connection() -> dict:
    result = await get_solar_radiation(lat=-23.5505, lng=-46.6333)
    return {
        "status": result.get("status"),
        "configured": True,
        "source": result.get("source", "nasa_power"),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

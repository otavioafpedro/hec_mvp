from datetime import datetime, timezone

import httpx

from app.config import settings


async def get_weather_data(station_id: str = "A701") -> dict:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{settings.INMET_BASE_URL}/estacao/dados/24/{station_id}",
            )
        if response.status_code != 200:
            return {"status": f"error_{response.status_code}", "station_id": station_id, "source": "inmet"}

        payload = response.json()
        latest = payload[-1] if payload else {}
        return {
            "status": "ok",
            "station_id": station_id,
            "temperature": _to_float(latest.get("TEM_INS")),
            "humidity": _to_float(latest.get("UMD_INS")),
            "radiation": _to_float(latest.get("RAD_GLO")),
            "wind_speed": _to_float(latest.get("VEN_VEL")),
            "pressure": _to_float(latest.get("PRE_INS")),
            "precipitation": _to_float(latest.get("CHUVA")),
            "source": "inmet",
        }
    except Exception as exc:  # pragma: no cover - runtime defensive path
        return {"status": f"error:{exc.__class__.__name__}", "station_id": station_id, "source": "inmet"}


def _to_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def test_connection() -> dict:
    result = await get_weather_data(station_id="A701")
    return {
        "status": result.get("status"),
        "configured": True,
        "source": "inmet",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

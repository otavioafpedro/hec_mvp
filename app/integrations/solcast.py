from datetime import datetime, timezone

import httpx

from app.config import settings


async def get_generation_estimate(lat: float, lng: float) -> dict:
    if not settings.SOLCAST_API_KEY:
        return {"status": "no_key", "kw_estimate": 0.0, "source": "solcast"}

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{settings.SOLCAST_BASE_URL}/world_radiation/estimated_actuals",
                params={
                    "latitude": lat,
                    "longitude": lng,
                    "format": "json",
                    "hours": 1,
                },
                headers={"Authorization": f"Bearer {settings.SOLCAST_API_KEY}"},
            )
        if response.status_code != 200:
            return {
                "status": f"error_{response.status_code}",
                "kw_estimate": 0.0,
                "source": "solcast",
            }

        payload = response.json()
        estimates = payload.get("estimated_actuals", [])
        latest = estimates[0] if estimates else {}
        ghi = latest.get("ghi", 0.0)
        return {
            "status": "ok",
            "kw_estimate": float(ghi or 0.0),
            "period_end": latest.get("period_end"),
            "source": "solcast",
        }
    except Exception as exc:  # pragma: no cover - runtime defensive path
        return {"status": f"error:{exc.__class__.__name__}", "kw_estimate": 0.0, "source": "solcast"}


async def test_connection() -> dict:
    if not settings.SOLCAST_API_KEY:
        return {"status": "not_configured", "configured": False}

    result = await get_generation_estimate(lat=-23.5505, lng=-46.6333)
    return {
        "status": result.get("status"),
        "configured": True,
        "source": "solcast",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

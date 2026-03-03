from fastapi import APIRouter

from app.integrations.service import get_integrations_status

router = APIRouter(prefix="/integrations", tags=["Integrations"])


@router.get("/status", summary="Status das integracoes externas")
async def integrations_status():
    return await get_integrations_status()

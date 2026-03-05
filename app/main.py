from fastapi import FastAPI

from app.config import settings
from app.api.health import router as health_router
from app.api.telemetry import router as telemetry_router
from app.api.inverter_telemetry import router as inverter_telemetry_router
from app.api.hec import router as hec_router
from app.api.lots import router as lots_router
from app.api.marketplace import router as marketplace_router
from app.api.burn import router as burn_router
from app.api.generator_onboarding import router as generator_onboarding_router

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=(
        "Validation Engine — Motor de validação de dados de geração solar "
        "distribuída para o Ecossistema Solar One / HUB ABSOLAR. "
        "Gera certificados HEC com lastro físico auditado."
    ),
)

app.include_router(health_router, tags=["Health"])
app.include_router(telemetry_router, tags=["Telemetry"])
app.include_router(inverter_telemetry_router)
app.include_router(hec_router)
app.include_router(lots_router)
app.include_router(marketplace_router)
app.include_router(burn_router)
app.include_router(generator_onboarding_router)


@app.get("/", tags=["Root"])
def root():
    return {
        "service": "validation-engine",
        "version": settings.VERSION,
        "docs": "/docs",
    }

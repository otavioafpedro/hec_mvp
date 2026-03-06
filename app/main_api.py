from fastapi import FastAPI

from app.config import settings
from app.api.health import router as health_router
from app.api.telemetry import router as telemetry_router
from app.api.inverter_telemetry import router as inverter_telemetry_router
from app.api.hec import router as hec_router
from app.api.integrations import router as integrations_router
from app.api.generator_onboarding import router as generator_onboarding_router
from app.api.consumer_pf import router as consumer_pf_router

app = FastAPI(
    title=f"{settings.PROJECT_NAME} [API]",
    version=settings.VERSION,
    description=(
        "HEC API Core - ingestao de telemetria, validacao e ciclo de certificados."
    ),
)

app.include_router(health_router, tags=["Health"])
app.include_router(telemetry_router, tags=["Telemetry"])
app.include_router(inverter_telemetry_router)
app.include_router(integrations_router)
app.include_router(hec_router)
app.include_router(generator_onboarding_router)
app.include_router(consumer_pf_router)


@app.get("/", tags=["Root"])
def root():
    return {
        "service": "hec-api-core",
        "layer": "api",
        "version": settings.VERSION,
        "docs": "/docs",
    }

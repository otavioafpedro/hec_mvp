from fastapi import FastAPI

from app.config import settings
from app.api.health import router as health_router
from app.api.marketplace import router as marketplace_router
from app.api.lots import router as lots_router
from app.api.burn import router as burn_router
from app.api.generator_onboarding import router as generator_onboarding_router
from app.api.consumer_pf import router as consumer_pf_router

app = FastAPI(
    title=f"{settings.PROJECT_NAME} [Consumer]",
    version=settings.VERSION,
    description=(
        "HEC Consumer API - marketplace, lotes e aposentadoria (burn) para cliente final."
    ),
)

app.include_router(health_router, tags=["Health"])
app.include_router(lots_router)
app.include_router(marketplace_router)
app.include_router(burn_router)
app.include_router(generator_onboarding_router)
app.include_router(consumer_pf_router)


@app.get("/", tags=["Root"])
def root():
    return {
        "service": "hec-consumer-api",
        "layer": "consumer",
        "version": settings.VERSION,
        "docs": "/docs",
    }

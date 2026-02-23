from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db

router = APIRouter()


@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    """
    Health check — verifica conectividade com o banco (PostgreSQL + TimescaleDB).
    Retorna 200 se tudo estiver operacional.
    """
    # Testa conexão com o banco
    result = db.execute(text("SELECT 1")).scalar()
    db_ok = result == 1

    # Verifica se TimescaleDB está disponível
    try:
        ts_version = db.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
        ).scalar()
        timescale_ok = ts_version is not None
    except Exception:
        ts_version = None
        timescale_ok = False

    return {
        "status": "healthy" if db_ok else "unhealthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "validation-engine",
        "version": "0.1.0",
        "checks": {
            "database": "ok" if db_ok else "fail",
            "timescaledb": ts_version or "not installed",
        },
    }

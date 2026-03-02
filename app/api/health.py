from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.db.soa_session import get_mysql_engine, get_timeseries_engine

router = APIRouter()


def _check_legacy_database(db: Session) -> dict:
    try:
        result = db.execute(text("SELECT 1")).scalar()
        db_ok = result == 1
    except Exception as exc:  # pragma: no cover - defensive in runtime
        return {"status": "fail", "error": exc.__class__.__name__}

    timescaledb_version = None
    try:
        timescaledb_version = db.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
        ).scalar()
    except Exception:
        timescaledb_version = None

    return {
        "status": "ok" if db_ok else "fail",
        "timescaledb": timescaledb_version or "not installed",
    }


def _check_soa_mariadb() -> dict:
    if not settings.SOA_ENABLE_INGEST:
        return {"status": "disabled"}

    try:
        with get_mysql_engine().connect() as conn:
            db_ok = conn.execute(text("SELECT 1")).scalar() == 1
            devices_table = conn.execute(text("SHOW TABLES LIKE 'devices'")).first()
    except Exception as exc:  # pragma: no cover - defensive in runtime
        return {"status": "fail", "error": exc.__class__.__name__}

    return {
        "status": "ok" if db_ok else "fail",
        "devices_table": "ok" if devices_table else "missing",
    }


def _check_soa_timeseries() -> dict:
    if not settings.SOA_ENABLE_INGEST:
        return {"status": "disabled"}

    try:
        with get_timeseries_engine().connect() as conn:
            db_ok = conn.execute(text("SELECT 1")).scalar() == 1
            timescaledb_version = conn.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
            ).scalar()
            inverter_telemetry_table = conn.execute(
                text("SELECT to_regclass('public.inverter_telemetry')")
            ).scalar()
    except Exception as exc:  # pragma: no cover - defensive in runtime
        return {"status": "fail", "error": exc.__class__.__name__}

    return {
        "status": "ok" if db_ok else "fail",
        "timescaledb": timescaledb_version or "not installed",
        "inverter_telemetry_table": "ok" if inverter_telemetry_table else "missing",
    }


@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    legacy_db = _check_legacy_database(db)
    soa_mariadb = _check_soa_mariadb()
    soa_timeseries = _check_soa_timeseries()

    required_ok = [legacy_db.get("status") == "ok"]
    if settings.SOA_ENABLE_INGEST:
        required_ok.append(soa_mariadb.get("status") == "ok")
        required_ok.append(
            soa_timeseries.get("status") == "ok"
            and soa_timeseries.get("inverter_telemetry_table") == "ok"
        )

    service_status = "healthy" if all(required_ok) else "unhealthy"

    return {
        "status": service_status,
        "timestamp": datetime.utcnow().isoformat(),
        "service": "validation-engine",
        "version": settings.VERSION,
        "soa_ingest_enabled": settings.SOA_ENABLE_INGEST,
        "checks": {
            "legacy_postgres": legacy_db,
            "soa_mariadb": soa_mariadb,
            "soa_timeseries": soa_timeseries,
        },
    }

from sqlalchemy import text

from app.config import settings
from app.db.soa_session import get_mysql_engine, get_timeseries_engine
from app.workers.common import run_forever

AGGREGATE_QUERY = text(
    """
    SELECT
        date_trunc('hour', ts) AS hour_ts,
        site_id,
        COUNT(*) AS readings,
        MIN(energy_today_wh) AS min_energy_today_wh,
        MAX(energy_today_wh) AS max_energy_today_wh
    FROM inverter_telemetry
    WHERE ts >= NOW() - (:lookback_hours || ' hours')::interval
    GROUP BY 1, 2
    ORDER BY 1 DESC, 2
    LIMIT :batch_size
    """
)

UPSERT_ENERGY_INTERVAL_QUERY = text(
    """
    INSERT INTO energy_intervals (
        ts,
        site_id,
        h3_index,
        interval_minutes,
        energy_generated_wh,
        inverter_readings_count,
        s1_inverter_available,
        data_completeness_pct,
        ready_for_qsv,
        qsv_processed
    ) VALUES (
        :ts,
        :site_id,
        :h3_index,
        60,
        :energy_generated_wh,
        :inverter_readings_count,
        TRUE,
        :data_completeness_pct,
        TRUE,
        FALSE
    )
    ON CONFLICT (ts, site_id) DO UPDATE
      SET h3_index = EXCLUDED.h3_index,
          energy_generated_wh = EXCLUDED.energy_generated_wh,
          inverter_readings_count = EXCLUDED.inverter_readings_count,
          s1_inverter_available = TRUE,
          data_completeness_pct = EXCLUDED.data_completeness_pct,
          ready_for_qsv = TRUE,
          qsv_processed = FALSE
    """
)


def _load_site_h3_map():
    if not settings.SOA_ENABLE_INGEST:
        return {}

    query = text("SELECT id, h3_index_res7 FROM sites")
    with get_mysql_engine().connect() as conn:
        rows = conn.execute(query).mappings().all()
    return {int(r["id"]): str(r["h3_index_res7"]) for r in rows}


def _step(logger):
    if not settings.SOA_ENABLE_INGEST:
        logger.info("soa_ingest_disabled skip=true")
        return

    h3_map = _load_site_h3_map()
    with get_timeseries_engine().connect() as conn:
        rows = conn.execute(
            AGGREGATE_QUERY,
            {
                "lookback_hours": settings.DS_LOOKBACK_HOURS,
                "batch_size": settings.WORKER_BATCH_SIZE,
            },
        ).mappings().all()

        processed = 0
        for row in rows:
            min_wh = row["min_energy_today_wh"] if row["min_energy_today_wh"] is not None else 0
            max_wh = row["max_energy_today_wh"] if row["max_energy_today_wh"] is not None else 0
            energy_generated = max(0, int(max_wh) - int(min_wh))
            readings = int(row["readings"])
            completeness = min(100.0, (readings / 60.0) * 100.0)
            site_id = int(row["site_id"])
            conn.execute(
                UPSERT_ENERGY_INTERVAL_QUERY,
                {
                    "ts": row["hour_ts"],
                    "site_id": site_id,
                    "h3_index": h3_map.get(site_id, "unknown"),
                    "energy_generated_wh": energy_generated,
                    "inverter_readings_count": readings,
                    "data_completeness_pct": completeness,
                },
            )
            processed += 1

        conn.commit()
        logger.info("ds_cross_validation_batch_processed count=%s", processed)


def run():
    run_forever("ds_cross_validation_worker", _step)


if __name__ == "__main__":
    run()

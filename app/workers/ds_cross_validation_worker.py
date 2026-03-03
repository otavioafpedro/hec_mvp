import asyncio
from collections import defaultdict

from sqlalchemy import text

from app.config import settings
from app.db.soa_session import get_mysql_engine, get_timeseries_engine
from app.integrations.service import collect_site_context
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

UPSERT_WEATHER_OBSERVATION_QUERY = text(
    """
    INSERT INTO weather_observations (
        ts,
        station_id,
        station_source,
        h3_index,
        latitude,
        longitude,
        ghi_wm2,
        temperature_c,
        relative_humidity_pct,
        wind_speed_ms,
        pressure_hpa,
        precipitation_mm,
        data_quality
    ) VALUES (
        :ts,
        :station_id,
        :station_source,
        :h3_index,
        :latitude,
        :longitude,
        :ghi_wm2,
        :temperature_c,
        :relative_humidity_pct,
        :wind_speed_ms,
        :pressure_hpa,
        :precipitation_mm,
        :data_quality
    )
    ON CONFLICT DO NOTHING
    """
)

UPSERT_SATELLITE_OBSERVATION_QUERY = text(
    """
    INSERT INTO satellite_observations (
        ts,
        h3_index,
        data_source,
        ghi_wm2,
        cloud_cover_pct,
        spatial_resolution_m,
        temporal_resolution_min,
        data_quality
    ) VALUES (
        :ts,
        :h3_index,
        :data_source,
        :ghi_wm2,
        :cloud_cover_pct,
        :spatial_resolution_m,
        :temporal_resolution_min,
        :data_quality
    )
    ON CONFLICT (ts, h3_index, data_source) DO UPDATE
      SET ghi_wm2 = EXCLUDED.ghi_wm2,
          cloud_cover_pct = EXCLUDED.cloud_cover_pct,
          spatial_resolution_m = EXCLUDED.spatial_resolution_m,
          temporal_resolution_min = EXCLUDED.temporal_resolution_min,
          data_quality = EXCLUDED.data_quality
    """
)

UPSERT_GRID_FACTOR_QUERY = text(
    """
    INSERT INTO grid_emission_factors_ts (
        ts,
        grid_region,
        factor_tco2_per_mwh,
        source
    ) VALUES (
        :ts,
        :grid_region,
        :factor_tco2_per_mwh,
        :source
    )
    ON CONFLICT (ts, grid_region) DO UPDATE
      SET factor_tco2_per_mwh = EXCLUDED.factor_tco2_per_mwh,
          source = EXCLUDED.source
    """
)


def _load_site_map():
    if not settings.SOA_ENABLE_INGEST:
        return {}

    query = text(
        """
        SELECT
            id,
            latitude,
            longitude,
            h3_index_res7
        FROM sites
        WHERE status IN ('active', 'maintenance')
        """
    )
    with get_mysql_engine().connect() as conn:
        rows = conn.execute(query).mappings().all()
    return {
        int(r["id"]): {
            "lat": float(r["latitude"]),
            "lng": float(r["longitude"]),
            "h3_index": (str(r["h3_index_res7"]) if r["h3_index_res7"] else f"site_{int(r['id'])}"),
        }
        for r in rows
    }


async def _fetch_context_for_sites(site_ids, site_map):
    contexts = {}
    for site_id in site_ids:
        site = site_map.get(site_id)
        if not site:
            continue
        contexts[site_id] = await collect_site_context(lat=site["lat"], lng=site["lng"])
    return contexts


def _persist_external_observations(conn, logger, site_rows, site_map):
    if not settings.DS_ENABLE_EXTERNAL_FETCH:
        logger.info("ds_external_fetch_disabled skip=true")
        return 0

    site_ids = sorted(site_rows.keys())
    contexts = asyncio.run(_fetch_context_for_sites(site_ids, site_map))
    persisted = 0

    for site_id in site_ids:
        if site_id not in contexts:
            continue

        site = site_map.get(site_id)
        if not site:
            continue

        row_ref = site_rows[site_id][0]
        ts_ref = row_ref["hour_ts"]
        context = contexts[site_id]

        weather = context.get("weather", {})
        weather_status = weather.get("status")
        if weather_status == "ok":
            conn.execute(
                UPSERT_WEATHER_OBSERVATION_QUERY,
                {
                    "ts": ts_ref,
                    "station_id": weather.get("station_id") or f"SITE{site_id}",
                    "station_source": weather.get("source") or "unknown",
                    "h3_index": site["h3_index"],
                    "latitude": site["lat"],
                    "longitude": site["lng"],
                    "ghi_wm2": weather.get("radiation"),
                    "temperature_c": weather.get("temperature"),
                    "relative_humidity_pct": weather.get("humidity"),
                    "wind_speed_ms": weather.get("wind_speed"),
                    "pressure_hpa": weather.get("pressure"),
                    "precipitation_mm": weather.get("precipitation"),
                    "data_quality": 95,
                },
            )
            persisted += 1

        satellite = context.get("satellite", {})
        sat_status = satellite.get("status")
        if sat_status == "ok":
            weather_fallback = context.get("weather_fallback", {})
            conn.execute(
                UPSERT_SATELLITE_OBSERVATION_QUERY,
                {
                    "ts": ts_ref,
                    "h3_index": site["h3_index"],
                    "data_source": satellite.get("source") or "nasa_power",
                    "ghi_wm2": satellite.get("ghi_wm2"),
                    "cloud_cover_pct": weather_fallback.get("clouds"),
                    "spatial_resolution_m": 40000,
                    "temporal_resolution_min": 60,
                    "data_quality": 90,
                },
            )
            persisted += 1

        carbon = context.get("carbon", {})
        carbon_intensity = carbon.get("carbonIntensity")
        if carbon.get("status") == "ok" and carbon_intensity is not None:
            # Electricity Maps returns gCO2/kWh. Convert to tCO2/MWh.
            factor_tco2_per_mwh = float(carbon_intensity) / 1000.0
            conn.execute(
                UPSERT_GRID_FACTOR_QUERY,
                {
                    "ts": ts_ref,
                    "grid_region": settings.DEFAULT_GRID_ZONE,
                    "factor_tco2_per_mwh": factor_tco2_per_mwh,
                    "source": "electricity_maps",
                },
            )
            persisted += 1

    return persisted


def _step(logger):
    if not settings.SOA_ENABLE_INGEST:
        logger.info("soa_ingest_disabled skip=true")
        return

    site_map = _load_site_map()
    with get_timeseries_engine().connect() as conn:
        rows = conn.execute(
            AGGREGATE_QUERY,
            {
                "lookback_hours": settings.DS_LOOKBACK_HOURS,
                "batch_size": settings.WORKER_BATCH_SIZE,
            },
        ).mappings().all()

        processed = 0
        site_rows = defaultdict(list)
        for row in rows:
            min_wh = row["min_energy_today_wh"] if row["min_energy_today_wh"] is not None else 0
            max_wh = row["max_energy_today_wh"] if row["max_energy_today_wh"] is not None else 0
            energy_generated = max(0, int(max_wh) - int(min_wh))
            readings = int(row["readings"])
            completeness = min(100.0, (readings / 60.0) * 100.0)
            site_id = int(row["site_id"])
            h3_index = site_map.get(site_id, {}).get("h3_index", f"site_{site_id}")
            conn.execute(
                UPSERT_ENERGY_INTERVAL_QUERY,
                {
                    "ts": row["hour_ts"],
                    "site_id": site_id,
                    "h3_index": h3_index,
                    "energy_generated_wh": energy_generated,
                    "inverter_readings_count": readings,
                    "data_completeness_pct": completeness,
                },
            )
            site_rows[site_id].append(row)
            processed += 1

        external_written = _persist_external_observations(conn, logger, site_rows, site_map)
        conn.commit()
        logger.info(
            "ds_cross_validation_batch_processed intervals=%s external_rows=%s",
            processed,
            external_written,
        )


def run():
    run_forever("ds_cross_validation_worker", _step)


if __name__ == "__main__":
    run()

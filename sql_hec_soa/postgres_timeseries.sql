-- =============================================================================
-- SOA/SOS — HEC/H-REC  ·  PostgreSQL 16 + TimescaleDB  ·  Time-Series Schema
-- =============================================================================
-- Versão:  1.0  ·  2026-02-26
-- Projeto: Solar One Account / Sustainability Operating System
-- Escopo:  Séries temporais e alta cardinalidade (telemetria, clima, features,
--          anomalias, leituras de inversores/medidores, energia por intervalo)
-- =============================================================================
-- Nota: Se TimescaleDB estiver disponível, usar hypertables (preferido).
--       Caso contrário, particionamento nativo por RANGE mensal está incluído.
-- =============================================================================

-- CREATE DATABASE soa_sos_ts
--   WITH ENCODING 'UTF8'
--        LC_COLLATE 'pt_BR.UTF-8'
--        LC_CTYPE   'pt_BR.UTF-8';

-- \c soa_sos_ts;

-- Tentar carregar TimescaleDB (falha silenciosa se não disponível)
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- UUID support
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ─────────────────────────────────────────────────────────────────────────────
-- TIPOS AUXILIARES
-- ─────────────────────────────────────────────────────────────────────────────

DO $$ BEGIN
    CREATE TYPE measurement_source AS ENUM ('inverter','meter','satellite','weather_station','neighbor','hybrid');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE signal_severity AS ENUM ('info','warning','critical');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. TELEMETRIA DE INVERSORES (leituras 1-min)
-- Alta cardinalidade: ~1440 registros/dia/inversor
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE inverter_telemetry (
    ts                  TIMESTAMPTZ     NOT NULL,
    device_id           BIGINT          NOT NULL,  -- ref: mysql devices.id
    site_id             BIGINT          NOT NULL,  -- ref: mysql sites.id
    -- Geração
    power_ac_w          REAL            NULL,       -- potência AC instantânea (W)
    power_dc_w          REAL            NULL,       -- potência DC instantânea (W)
    energy_today_wh     BIGINT          NULL,       -- energia acumulada do dia (Wh)
    energy_total_wh     BIGINT          NULL,       -- energia acumulada total (Wh)
    -- Tensão / Corrente
    voltage_ac_v        REAL            NULL,
    current_ac_a        REAL            NULL,
    voltage_dc_v        REAL            NULL,
    current_dc_a        REAL            NULL,
    -- Performance
    frequency_hz        REAL            NULL,
    efficiency_pct      REAL            NULL,       -- 0-100
    temperature_c       REAL            NULL,       -- temperatura interna inversor
    -- Status
    status_code         SMALLINT        NULL,
    error_code          SMALLINT        NULL,
    is_online           BOOLEAN         NOT NULL DEFAULT TRUE,
    -- Qualidade
    data_quality        SMALLINT        NOT NULL DEFAULT 100  -- 0-100
);

-- Remover placeholders de comentário (PostgreSQL não suporta COMMENT inline)
COMMENT ON COLUMN inverter_telemetry.power_ac_w IS 'Potência AC instantânea em Watts';
COMMENT ON COLUMN inverter_telemetry.energy_today_wh IS 'Energia acumulada no dia em Wh';

-- TimescaleDB hypertable (chunk de 1 dia)
SELECT create_hypertable('inverter_telemetry', 'ts',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- Índices
CREATE INDEX idx_inv_tel_device_ts  ON inverter_telemetry (device_id, ts DESC);
CREATE INDEX idx_inv_tel_site_ts    ON inverter_telemetry (site_id, ts DESC);
CREATE INDEX idx_inv_tel_ts_brin    ON inverter_telemetry USING BRIN (ts) WITH (pages_per_range = 32);

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. LEITURAS DE MEDIDORES (meter readings, 1-15 min)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE meter_readings (
    ts                      TIMESTAMPTZ     NOT NULL,
    device_id               BIGINT          NOT NULL,
    site_id                 BIGINT          NOT NULL,
    -- Energia
    energy_import_wh        BIGINT          NULL,       -- consumo da rede (Wh)
    energy_export_wh        BIGINT          NULL,       -- injeção na rede (Wh)
    energy_generation_wh    BIGINT          NULL,       -- geração total (Wh)
    energy_consumption_wh   BIGINT          NULL,       -- consumo local (Wh)
    -- Potência
    power_import_w          REAL            NULL,
    power_export_w          REAL            NULL,
    -- Grid
    voltage_grid_v          REAL            NULL,
    frequency_grid_hz       REAL            NULL,
    power_factor            REAL            NULL,       -- fator de potência
    -- Qualidade
    data_quality            SMALLINT        NOT NULL DEFAULT 100
);

COMMENT ON COLUMN meter_readings.energy_import_wh IS 'Energia importada da rede (Wh)';
COMMENT ON COLUMN meter_readings.energy_export_wh IS 'Energia exportada para rede (Wh)';

SELECT create_hypertable('meter_readings', 'ts',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

CREATE INDEX idx_meter_device_ts ON meter_readings (device_id, ts DESC);
CREATE INDEX idx_meter_site_ts   ON meter_readings (site_id, ts DESC);
CREATE INDEX idx_meter_ts_brin   ON meter_readings USING BRIN (ts) WITH (pages_per_range = 32);

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. ENERGIA POR INTERVALO (agregação horária — base do HEC)
-- 1 registro por hora por site = ~8760/ano/site
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE energy_intervals (
    ts                      TIMESTAMPTZ     NOT NULL,   -- início do intervalo (hora cheia UTC)
    site_id                 BIGINT          NOT NULL,
    h3_index                VARCHAR(20)     NOT NULL,
    interval_minutes        SMALLINT        NOT NULL DEFAULT 60,
    -- Energia
    energy_generated_wh     BIGINT          NOT NULL DEFAULT 0,
    energy_exported_wh      BIGINT          NOT NULL DEFAULT 0,
    energy_self_consumed_wh BIGINT          NOT NULL DEFAULT 0,
    energy_imported_wh      BIGINT          NOT NULL DEFAULT 0,
    energy_curtailed_wh     BIGINT          NOT NULL DEFAULT 0,
    -- Fonte de mensuração
    measurement_source      measurement_source NOT NULL DEFAULT 'inverter',
    inverter_readings_count SMALLINT        NOT NULL DEFAULT 0,
    meter_readings_count    SMALLINT        NOT NULL DEFAULT 0,
    -- QSV parcial (pré-validação)
    s1_inverter_available   BOOLEAN         NOT NULL DEFAULT FALSE,
    s2_satellite_available  BOOLEAN         NOT NULL DEFAULT FALSE,
    s3_weather_available    BOOLEAN         NOT NULL DEFAULT FALSE,
    s4_neighbor_available   BOOLEAN         NOT NULL DEFAULT FALSE,
    data_completeness_pct   REAL            NOT NULL DEFAULT 0,
    -- Flag de pronto para QSV
    ready_for_qsv           BOOLEAN         NOT NULL DEFAULT FALSE,
    qsv_processed           BOOLEAN         NOT NULL DEFAULT FALSE
);

SELECT create_hypertable('energy_intervals', 'ts',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

CREATE UNIQUE INDEX uq_energy_interval ON energy_intervals (ts, site_id);
CREATE INDEX idx_energy_site_ts     ON energy_intervals (site_id, ts DESC);
CREATE INDEX idx_energy_h3_ts       ON energy_intervals (h3_index, ts DESC);
CREATE INDEX idx_energy_ready       ON energy_intervals (ready_for_qsv, qsv_processed) WHERE ready_for_qsv = TRUE AND qsv_processed = FALSE;
CREATE INDEX idx_energy_ts_brin     ON energy_intervals USING BRIN (ts) WITH (pages_per_range = 32);

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. FEATURES POR INTERVALO (saída do pipeline ML para QSV)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE interval_features (
    ts                      TIMESTAMPTZ     NOT NULL,
    site_id                 BIGINT          NOT NULL,
    run_id                  BIGINT          NOT NULL,   -- ref: mysql ds_pipeline_runs.id
    -- Features S1 — Inversor
    f_inverter_power_mean   REAL            NULL,
    f_inverter_power_std    REAL            NULL,
    f_inverter_power_max    REAL            NULL,
    f_inverter_temp_mean    REAL            NULL,
    f_inverter_efficiency   REAL            NULL,
    f_inverter_uptime_pct   REAL            NULL,
    -- Features S2 — Satélite (Copernicus)
    f_sat_ghi_wm2           REAL            NULL,       -- Global Horizontal Irradiance
    f_sat_dni_wm2           REAL            NULL,       -- Direct Normal Irradiance
    f_sat_dhi_wm2           REAL            NULL,       -- Diffuse Horizontal Irradiance
    f_sat_cloud_cover_pct   REAL            NULL,
    f_sat_aerosol_depth     REAL            NULL,
    -- Features S3 — Estação meteorológica (INMET/SONDA)
    f_wx_ghi_wm2            REAL            NULL,
    f_wx_temperature_c      REAL            NULL,
    f_wx_humidity_pct       REAL            NULL,
    f_wx_wind_speed_ms      REAL            NULL,
    f_wx_pressure_hpa       REAL            NULL,
    f_wx_precipitation_mm   REAL            NULL,
    -- Features S4 — Vizinhos
    f_neighbor_count        SMALLINT        NULL,
    f_neighbor_power_mean   REAL            NULL,
    f_neighbor_agreement    REAL            NULL,       -- 0-1 concordância
    -- Features derivadas
    f_ghi_ratio_sat_wx      REAL            NULL,       -- S2/S3 ratio
    f_power_vs_expected     REAL            NULL,       -- real vs pvlib teórico
    f_capacity_factor       REAL            NULL,       -- fator de capacidade
    f_ramp_rate             REAL            NULL,       -- variação potência
    -- Modelo
    model_name              VARCHAR(100)    NULL,
    model_version           VARCHAR(50)     NULL
);

SELECT create_hypertable('interval_features', 'ts',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

CREATE UNIQUE INDEX uq_int_features ON interval_features (ts, site_id, run_id);
CREATE INDEX idx_feat_site_ts       ON interval_features (site_id, ts DESC);
CREATE INDEX idx_feat_run           ON interval_features (run_id, ts DESC);
CREATE INDEX idx_feat_ts_brin       ON interval_features USING BRIN (ts) WITH (pages_per_range = 32);

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. QSV SCORES POR INTERVALO (resultado da validação)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE qsv_scores (
    ts                      TIMESTAMPTZ     NOT NULL,
    site_id                 BIGINT          NOT NULL,
    run_id                  BIGINT          NOT NULL,
    -- Score
    qsv_score               SMALLINT        NOT NULL CHECK (qsv_score BETWEEN 0 AND 3),
    p_truth                 REAL            NOT NULL CHECK (p_truth BETWEEN 0 AND 1),
    -- Detalhe por fonte
    s1_score                REAL            NULL,       -- 0-1
    s2_score                REAL            NULL,
    s3_score                REAL            NULL,
    s4_score                REAL            NULL,
    -- Comparações A/B/C
    comparison_a_result     REAL            NULL,       -- inversor vs satélite
    comparison_b_result     REAL            NULL,       -- inversor vs estação
    comparison_c_result     REAL            NULL,       -- vizinhos cross-check
    -- Decisão
    is_eligible             BOOLEAN         NOT NULL DEFAULT FALSE,
    decision_reason         VARCHAR(500)    NULL,
    -- Energia validada
    validated_energy_wh     BIGINT          NULL,
    -- Referência a certificado (preenchido após mint)
    certificate_id          BIGINT          NULL        -- ref: mysql certificates.id
);

SELECT create_hypertable('qsv_scores', 'ts',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

CREATE UNIQUE INDEX uq_qsv_score ON qsv_scores (ts, site_id, run_id);
CREATE INDEX idx_qsv_site_ts    ON qsv_scores (site_id, ts DESC);
CREATE INDEX idx_qsv_eligible   ON qsv_scores (is_eligible, ts DESC) WHERE is_eligible = TRUE;
CREATE INDEX idx_qsv_cert       ON qsv_scores (certificate_id) WHERE certificate_id IS NOT NULL;
CREATE INDEX idx_qsv_ts_brin    ON qsv_scores USING BRIN (ts) WITH (pages_per_range = 32);

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. SINAIS / ANOMALIAS (detecção por LSTM autoencoder e regras)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE anomaly_signals (
    ts                      TIMESTAMPTZ     NOT NULL,
    site_id                 BIGINT          NOT NULL,
    device_id               BIGINT          NULL,
    run_id                  BIGINT          NULL,
    -- Anomalia
    signal_type             VARCHAR(80)     NOT NULL,   -- 'power_drop','ghi_mismatch','temp_spike','meter_drift','neighbor_disagree','curtailment'
    severity                signal_severity NOT NULL DEFAULT 'info',
    score                   REAL            NOT NULL,   -- anomaly score (>2σ = flag)
    threshold               REAL            NULL,       -- limiar usado
    -- Contexto
    expected_value          REAL            NULL,
    actual_value            REAL            NULL,
    deviation_pct           REAL            NULL,
    description             VARCHAR(1000)   NULL,
    -- Ação
    auto_action             VARCHAR(100)    NULL,       -- 'flag_certificate','trigger_icleansolar','block_mint','none'
    acknowledged            BOOLEAN         NOT NULL DEFAULT FALSE,
    acknowledged_by         BIGINT          NULL,       -- ref: mysql users.id
    acknowledged_at         TIMESTAMPTZ     NULL
);

SELECT create_hypertable('anomaly_signals', 'ts',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

CREATE INDEX idx_anomaly_site_ts    ON anomaly_signals (site_id, ts DESC);
CREATE INDEX idx_anomaly_device_ts  ON anomaly_signals (device_id, ts DESC) WHERE device_id IS NOT NULL;
CREATE INDEX idx_anomaly_severity   ON anomaly_signals (severity, ts DESC);
CREATE INDEX idx_anomaly_type       ON anomaly_signals (signal_type, ts DESC);
CREATE INDEX idx_anomaly_ts_brin    ON anomaly_signals USING BRIN (ts) WITH (pages_per_range = 32);

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. DADOS METEOROLÓGICOS — INMET / SONDA
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE weather_observations (
    ts                      TIMESTAMPTZ     NOT NULL,
    station_id              VARCHAR(30)     NOT NULL,   -- código INMET (e.g. A001) ou SONDA
    station_source          VARCHAR(20)     NOT NULL DEFAULT 'inmet',  -- 'inmet','sonda_inpe','nasa_power'
    h3_index                VARCHAR(20)     NULL,
    latitude                REAL            NOT NULL,
    longitude               REAL            NOT NULL,
    -- Irradiância
    ghi_wm2                 REAL            NULL,       -- Global Horizontal Irradiance
    dni_wm2                 REAL            NULL,       -- Direct Normal Irradiance
    dhi_wm2                 REAL            NULL,       -- Diffuse Horizontal Irradiance
    -- Meteorologia
    temperature_c           REAL            NULL,
    relative_humidity_pct   REAL            NULL,
    wind_speed_ms           REAL            NULL,
    wind_direction_deg      REAL            NULL,
    pressure_hpa            REAL            NULL,
    precipitation_mm        REAL            NULL,
    -- Qualidade
    data_quality            SMALLINT        NOT NULL DEFAULT 100
);

SELECT create_hypertable('weather_observations', 'ts',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

CREATE INDEX idx_wx_station_ts  ON weather_observations (station_id, ts DESC);
CREATE INDEX idx_wx_h3_ts       ON weather_observations (h3_index, ts DESC) WHERE h3_index IS NOT NULL;
CREATE INDEX idx_wx_source_ts   ON weather_observations (station_source, ts DESC);
CREATE INDEX idx_wx_ts_brin     ON weather_observations USING BRIN (ts) WITH (pages_per_range = 32);

-- ─────────────────────────────────────────────────────────────────────────────
-- 8. DADOS SATELITAIS — COPERNICUS / INPE / NASA POWER
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE satellite_observations (
    ts                      TIMESTAMPTZ     NOT NULL,
    h3_index                VARCHAR(20)     NOT NULL,   -- cobertura regional
    data_source             VARCHAR(30)     NOT NULL DEFAULT 'copernicus',  -- 'copernicus','inpe_dsa','nasa_power','goes16'
    -- Irradiância
    ghi_wm2                 REAL            NULL,
    dni_wm2                 REAL            NULL,
    dhi_wm2                 REAL            NULL,
    -- Atmosfera
    cloud_cover_pct         REAL            NULL,
    cloud_type              VARCHAR(30)     NULL,
    aerosol_optical_depth   REAL            NULL,
    total_column_ozone_du   REAL            NULL,
    precipitable_water_mm   REAL            NULL,
    -- Resolução
    spatial_resolution_m    INT             NULL,       -- resolução espacial em metros
    temporal_resolution_min INT             NULL,       -- resolução temporal em minutos
    -- Qualidade
    data_quality            SMALLINT        NOT NULL DEFAULT 100
);

SELECT create_hypertable('satellite_observations', 'ts',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

CREATE UNIQUE INDEX uq_sat_obs ON satellite_observations (ts, h3_index, data_source);
CREATE INDEX idx_sat_h3_ts      ON satellite_observations (h3_index, ts DESC);
CREATE INDEX idx_sat_source_ts  ON satellite_observations (data_source, ts DESC);
CREATE INDEX idx_sat_ts_brin    ON satellite_observations USING BRIN (ts) WITH (pages_per_range = 32);

-- ─────────────────────────────────────────────────────────────────────────────
-- 9. SDK BURN TELEMETRY (segundo a segundo — PoCE/Leaf Bar)
-- Altíssima cardinalidade: potencialmente milhões de registros/dia
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE sdk_burn_telemetry (
    ts                      TIMESTAMPTZ     NOT NULL,
    user_id                 BIGINT          NOT NULL,   -- ref: mysql users.id
    session_id              BIGINT          NOT NULL,   -- ref: mysql poce_sessions.id
    -- Consumo segundo a segundo
    energy_consumed_wh      REAL            NOT NULL DEFAULT 0,
    mhec_consumed           REAL            NOT NULL DEFAULT 0,
    -- Contexto
    matching_source_site_id BIGINT          NULL,       -- site que fornece o mHEC
    grid_emission_factor    REAL            NULL,       -- tCO₂/MWh naquele instante
    is_green                BOOLEAN         NOT NULL DEFAULT FALSE,  -- TRUE = queimando mHEC
    -- Leaf Bar state
    leaf_bar_pct            REAL            NULL,       -- 0-100% verde
    cumulative_session_wh   REAL            NOT NULL DEFAULT 0
);

SELECT create_hypertable('sdk_burn_telemetry', 'ts',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

CREATE INDEX idx_sdk_user_ts    ON sdk_burn_telemetry (user_id, ts DESC);
CREATE INDEX idx_sdk_session_ts ON sdk_burn_telemetry (session_id, ts DESC);
CREATE INDEX idx_sdk_ts_brin    ON sdk_burn_telemetry USING BRIN (ts) WITH (pages_per_range = 16);

-- ─────────────────────────────────────────────────────────────────────────────
-- 10. EMISSION FACTOR TIMESERIES (SIN/ONS horário)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE grid_emission_factors_ts (
    ts                      TIMESTAMPTZ     NOT NULL,
    grid_region             VARCHAR(30)     NOT NULL DEFAULT 'SIN',  -- 'SIN','SE_CO','S','NE','N'
    factor_tco2_per_mwh     REAL            NOT NULL,
    source                  VARCHAR(50)     NOT NULL DEFAULT 'ons',  -- 'ons','mcti','chainlink'
    aneel_flag              VARCHAR(10)     NULL,       -- 'green','yellow','red'
    generation_mix_json     JSONB           NULL        -- {hydro: 0.65, thermal: 0.15, wind: 0.12, solar: 0.08}
);

SELECT create_hypertable('grid_emission_factors_ts', 'ts',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE
);

CREATE UNIQUE INDEX uq_grid_ef ON grid_emission_factors_ts (ts, grid_region);
CREATE INDEX idx_grid_ef_ts_brin ON grid_emission_factors_ts USING BRIN (ts) WITH (pages_per_range = 32);

-- ─────────────────────────────────────────────────────────────────────────────
-- 11. NEIGHBOR CROSS-VALIDATION (S4 — dados de vizinhos por hexágono)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE neighbor_validations (
    ts                      TIMESTAMPTZ     NOT NULL,
    h3_index                VARCHAR(20)     NOT NULL,
    target_site_id          BIGINT          NOT NULL,
    neighbor_site_id        BIGINT          NOT NULL,
    -- Comparação
    target_power_w          REAL            NULL,
    neighbor_power_w        REAL            NULL,
    capacity_ratio          REAL            NULL,       -- normalizado por kWp
    normalized_deviation    REAL            NULL,       -- desvio normalizado
    agreement_score         REAL            NULL,       -- 0-1
    is_corroborated         BOOLEAN         NOT NULL DEFAULT FALSE
);

SELECT create_hypertable('neighbor_validations', 'ts',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

CREATE INDEX idx_neighbor_target_ts ON neighbor_validations (target_site_id, ts DESC);
CREATE INDEX idx_neighbor_h3_ts     ON neighbor_validations (h3_index, ts DESC);
CREATE INDEX idx_neighbor_ts_brin   ON neighbor_validations USING BRIN (ts) WITH (pages_per_range = 32);

-- ─────────────────────────────────────────────────────────────────────────────
-- 12. PVLIB THEORETICAL OUTPUT (teórico de pvlib por hora/site)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE pvlib_theoretical (
    ts                      TIMESTAMPTZ     NOT NULL,
    site_id                 BIGINT          NOT NULL,
    -- Teórico
    expected_power_w        REAL            NOT NULL,
    expected_energy_wh      BIGINT          NOT NULL,
    -- Parâmetros
    ghi_input_wm2           REAL            NULL,
    module_temp_c           REAL            NULL,
    poa_irradiance_wm2      REAL            NULL,       -- plane-of-array
    dc_power_w              REAL            NULL,
    ac_power_w              REAL            NULL,
    -- Config
    model_config_hash       VARCHAR(66)     NULL        -- hash da configuração pvlib usada
);

SELECT create_hypertable('pvlib_theoretical', 'ts',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

CREATE UNIQUE INDEX uq_pvlib ON pvlib_theoretical (ts, site_id);
CREATE INDEX idx_pvlib_site_ts  ON pvlib_theoretical (site_id, ts DESC);
CREATE INDEX idx_pvlib_ts_brin  ON pvlib_theoretical USING BRIN (ts) WITH (pages_per_range = 32);

-- ─────────────────────────────────────────────────────────────────────────────
-- 13. CONTINUOUS AGGREGATES (TimescaleDB materialized views)
-- ─────────────────────────────────────────────────────────────────────────────

-- Agregação horária da telemetria de inversores (para alimentar energy_intervals)
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_inverter_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', ts)           AS bucket,
    site_id,
    device_id,
    AVG(power_ac_w)                     AS avg_power_w,
    MAX(power_ac_w)                     AS max_power_w,
    MIN(power_ac_w)                     AS min_power_w,
    STDDEV(power_ac_w)                  AS std_power_w,
    MAX(energy_today_wh) - MIN(energy_today_wh) AS delta_energy_wh,
    AVG(temperature_c)                  AS avg_temp_c,
    AVG(efficiency_pct)                 AS avg_efficiency,
    COUNT(*)                            AS reading_count,
    AVG(data_quality)                   AS avg_data_quality
FROM inverter_telemetry
GROUP BY bucket, site_id, device_id
WITH NO DATA;

-- Refresh policy: a cada 30 minutos, materializar dados de até 2h atrás
SELECT add_continuous_aggregate_policy('mv_inverter_hourly',
    start_offset    => INTERVAL '2 hours',
    end_offset      => INTERVAL '30 minutes',
    schedule_interval => INTERVAL '30 minutes',
    if_not_exists   => TRUE
);

-- Agregação diária por site
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_energy_daily
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', ts)            AS bucket,
    site_id,
    h3_index,
    SUM(energy_generated_wh)            AS total_generated_wh,
    SUM(energy_exported_wh)             AS total_exported_wh,
    SUM(energy_self_consumed_wh)        AS total_self_consumed_wh,
    SUM(energy_curtailed_wh)            AS total_curtailed_wh,
    COUNT(*)                            AS interval_count,
    AVG(data_completeness_pct)          AS avg_completeness
FROM energy_intervals
GROUP BY bucket, site_id, h3_index
WITH NO DATA;

SELECT add_continuous_aggregate_policy('mv_energy_daily',
    start_offset    => INTERVAL '2 days',
    end_offset      => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists   => TRUE
);

-- ─────────────────────────────────────────────────────────────────────────────
-- 14. RETENTION POLICIES (limpeza automática de dados antigos)
-- ─────────────────────────────────────────────────────────────────────────────

-- Telemetria bruta de inversores: manter 2 anos (agregados ficam para sempre)
SELECT add_retention_policy('inverter_telemetry', INTERVAL '2 years', if_not_exists => TRUE);

-- SDK burn telemetry: manter 1 ano (altíssimo volume)
SELECT add_retention_policy('sdk_burn_telemetry', INTERVAL '1 year', if_not_exists => TRUE);

-- Meter readings: manter 3 anos
SELECT add_retention_policy('meter_readings', INTERVAL '3 years', if_not_exists => TRUE);

-- Dados de satélite e clima: manter 5 anos (referência científica)
SELECT add_retention_policy('weather_observations', INTERVAL '5 years', if_not_exists => TRUE);
SELECT add_retention_policy('satellite_observations', INTERVAL '5 years', if_not_exists => TRUE);

-- Energy intervals, QSV scores, features: sem retention (permanente)

-- ─────────────────────────────────────────────────────────────────────────────
-- 15. COMPRESSION POLICIES (compressão de chunks antigos)
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE inverter_telemetry SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id',
    timescaledb.compress_orderby = 'ts DESC'
);
SELECT add_compression_policy('inverter_telemetry', INTERVAL '7 days', if_not_exists => TRUE);

ALTER TABLE meter_readings SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id',
    timescaledb.compress_orderby = 'ts DESC'
);
SELECT add_compression_policy('meter_readings', INTERVAL '7 days', if_not_exists => TRUE);

ALTER TABLE sdk_burn_telemetry SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'user_id',
    timescaledb.compress_orderby = 'ts DESC'
);
SELECT add_compression_policy('sdk_burn_telemetry', INTERVAL '3 days', if_not_exists => TRUE);

ALTER TABLE weather_observations SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'station_id',
    timescaledb.compress_orderby = 'ts DESC'
);
SELECT add_compression_policy('weather_observations', INTERVAL '30 days', if_not_exists => TRUE);

ALTER TABLE satellite_observations SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'h3_index',
    timescaledb.compress_orderby = 'ts DESC'
);
SELECT add_compression_policy('satellite_observations', INTERVAL '30 days', if_not_exists => TRUE);

-- ─────────────────────────────────────────────────────────────────────────────
-- FIM DO SCHEMA POSTGRESQL / TIMESCALEDB
-- ─────────────────────────────────────────────────────────────────────────────

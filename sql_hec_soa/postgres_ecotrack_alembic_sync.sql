-- =============================================================================
-- ecotrack_postgres_alembic_sync.sql
-- Purpose: Apply the Alembic 001-019 data model as raw SQL (idempotent)
-- Target : PostgreSQL (same DB used by DATABASE_URL, e.g. validation_engine)
-- Notes  :
--   1) Run AFTER sql_hec_soa/postgres_timeseries.sql
--   2) Safe to rerun (IF NOT EXISTS / guarded constraints)
-- =============================================================================

-- NOTE:
--   This script is extension-agnostic by design.
--   It does not require or attempt to install TimescaleDB/PostGIS.
--   All objects below run on plain PostgreSQL.

-- -----------------------------------------------------------------------------
-- 1) Marketplace/auth base tables (Alembic 010+)
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'buyer',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email ON users (email);
CREATE INDEX IF NOT EXISTS ix_users_email ON users (email);

CREATE TABLE IF NOT EXISTS wallets (
    wallet_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE REFERENCES users(user_id),
    balance_brl NUMERIC(16, 2) NOT NULL DEFAULT 0,
    hec_balance INTEGER NOT NULL DEFAULT 0,
    energy_balance_kwh NUMERIC(16, 4) NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NULL
);

-- -----------------------------------------------------------------------------
-- 2) Core validation/certificate tables (Alembic 001-012)
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS plants (
    plant_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    absolar_id VARCHAR(100) UNIQUE,
    owner_name VARCHAR(255),
    owner_user_id UUID NULL,
    lat DOUBLE PRECISION NOT NULL,
    lng DOUBLE PRECISION NOT NULL,
    capacity_kw NUMERIC(12, 3) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    inverter_brand VARCHAR(100),
    inverter_model VARCHAR(100),
    commissioning_date TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE plants
    ADD COLUMN IF NOT EXISTS owner_user_id UUID;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_plants_owner_user_id_users'
    ) THEN
        ALTER TABLE plants
            ADD CONSTRAINT fk_plants_owner_user_id_users
            FOREIGN KEY (owner_user_id) REFERENCES users(user_id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_plants_owner_user_id ON plants (owner_user_id);

CREATE INDEX IF NOT EXISTS idx_plants_status_lat_lng ON plants (status, lat, lng);

CREATE TABLE IF NOT EXISTS hec_lots (
    lot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    total_energy_kwh NUMERIC(16, 4) NOT NULL DEFAULT 0,
    total_quantity INTEGER NOT NULL DEFAULT 0,
    available_quantity INTEGER NOT NULL DEFAULT 0,
    certificate_count INTEGER NOT NULL DEFAULT 0,
    price_per_kwh NUMERIC(10, 4),
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE hec_lots
    ADD COLUMN IF NOT EXISTS total_quantity INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS available_quantity INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS price_per_kwh NUMERIC(10, 4);

CREATE TABLE IF NOT EXISTS telemetry (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    time TIMESTAMP NOT NULL,
    plant_id UUID NOT NULL REFERENCES plants(plant_id),
    power_kw NUMERIC(12, 4) NOT NULL,
    energy_kwh NUMERIC(14, 4) NOT NULL,
    voltage_v NUMERIC(8, 2),
    temperature_c NUMERIC(6, 2),
    irradiance_wm2 NUMERIC(8, 2),
    source VARCHAR(50) NOT NULL DEFAULT 'mqtt',
    pre_commitment_hash VARCHAR(256),
    ntp_delta_ms DOUBLE PRECISION,
    ntp_pass BOOLEAN,
    raw_payload JSONB,
    payload_sha256 VARCHAR(64),
    nonce VARCHAR(64),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (time, id)
);

ALTER TABLE telemetry
    ADD COLUMN IF NOT EXISTS ntp_pass BOOLEAN,
    ADD COLUMN IF NOT EXISTS raw_payload JSONB,
    ADD COLUMN IF NOT EXISTS payload_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS nonce VARCHAR(64),
    ADD COLUMN IF NOT EXISTS ntp_delta_ms DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS pre_commitment_hash VARCHAR(256);

ALTER TABLE telemetry
    ALTER COLUMN pre_commitment_hash TYPE VARCHAR(256);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'telemetry'::regclass
          AND contype = 'p'
    ) THEN
        ALTER TABLE telemetry ADD PRIMARY KEY (time, id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_telemetry_time ON telemetry (time);
CREATE INDEX IF NOT EXISTS ix_telemetry_plant_id ON telemetry (plant_id);
CREATE INDEX IF NOT EXISTS ix_telemetry_nonce ON telemetry (nonce);

-- telemetry remains a regular PostgreSQL table (no TimescaleDB hypertable conversion).

CREATE TABLE IF NOT EXISTS validations (
    validation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plant_id UUID NOT NULL REFERENCES plants(plant_id),
    telemetry_id UUID,
    period_start TIMESTAMP NOT NULL,
    period_end TIMESTAMP NOT NULL,
    energy_kwh NUMERIC(14, 4) NOT NULL,
    confidence_score NUMERIC(5, 2) NOT NULL,
    anomaly_flags JSONB,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    ntp_pass BOOLEAN,
    ntp_drift_ms DOUBLE PRECISION,
    theoretical_max_kwh NUMERIC(14, 4),
    theoretical_max_kw NUMERIC(12, 4),
    ghi_clear_sky_wm2 NUMERIC(8, 2),
    solar_elevation_deg NUMERIC(6, 2),
    physics_pass BOOLEAN,
    physics_method VARCHAR(20),
    satellite_ghi_wm2 NUMERIC(8, 2),
    satellite_source VARCHAR(30),
    satellite_max_kwh NUMERIC(14, 4),
    satellite_pass BOOLEAN,
    cloud_cover_pct NUMERIC(5, 1),
    satellite_flags JSONB,
    consensus_pass BOOLEAN,
    consensus_deviation_pct NUMERIC(6, 2),
    consensus_median_ratio NUMERIC(10, 6),
    consensus_plant_ratio NUMERIC(10, 6),
    consensus_neighbors INTEGER,
    consensus_radius_km NUMERIC(6, 2),
    consensus_details JSONB,
    sentinel_version VARCHAR(20),
    validation_details JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE validations
    ADD COLUMN IF NOT EXISTS telemetry_id UUID,
    ADD COLUMN IF NOT EXISTS ntp_pass BOOLEAN,
    ADD COLUMN IF NOT EXISTS ntp_drift_ms DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS theoretical_max_kwh NUMERIC(14, 4),
    ADD COLUMN IF NOT EXISTS theoretical_max_kw NUMERIC(12, 4),
    ADD COLUMN IF NOT EXISTS ghi_clear_sky_wm2 NUMERIC(8, 2),
    ADD COLUMN IF NOT EXISTS solar_elevation_deg NUMERIC(6, 2),
    ADD COLUMN IF NOT EXISTS physics_pass BOOLEAN,
    ADD COLUMN IF NOT EXISTS physics_method VARCHAR(20),
    ADD COLUMN IF NOT EXISTS satellite_ghi_wm2 NUMERIC(8, 2),
    ADD COLUMN IF NOT EXISTS satellite_source VARCHAR(30),
    ADD COLUMN IF NOT EXISTS satellite_max_kwh NUMERIC(14, 4),
    ADD COLUMN IF NOT EXISTS satellite_pass BOOLEAN,
    ADD COLUMN IF NOT EXISTS cloud_cover_pct NUMERIC(5, 1),
    ADD COLUMN IF NOT EXISTS satellite_flags JSONB,
    ADD COLUMN IF NOT EXISTS consensus_pass BOOLEAN,
    ADD COLUMN IF NOT EXISTS consensus_deviation_pct NUMERIC(6, 2),
    ADD COLUMN IF NOT EXISTS consensus_median_ratio NUMERIC(10, 6),
    ADD COLUMN IF NOT EXISTS consensus_plant_ratio NUMERIC(10, 6),
    ADD COLUMN IF NOT EXISTS consensus_neighbors INTEGER,
    ADD COLUMN IF NOT EXISTS consensus_radius_km NUMERIC(6, 2),
    ADD COLUMN IF NOT EXISTS consensus_details JSONB;

CREATE INDEX IF NOT EXISTS ix_validations_plant_id ON validations (plant_id);

CREATE TABLE IF NOT EXISTS hec_certificates (
    hec_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    validation_id UUID NOT NULL UNIQUE REFERENCES validations(validation_id),
    lot_id UUID NULL REFERENCES hec_lots(lot_id),
    hash_sha256 VARCHAR(64) NOT NULL UNIQUE,
    energy_kwh NUMERIC(14, 4) NOT NULL,
    certificate_json JSONB,
    token_id VARCHAR(100),
    contract_address VARCHAR(42),
    chain VARCHAR(20) DEFAULT 'polygon',
    ipfs_cid VARCHAR(100),
    ipfs_json_cid VARCHAR(100),
    ipfs_pdf_cid VARCHAR(100),
    ipfs_provider VARCHAR(20),
    registry_tx_hash VARCHAR(66),
    registry_block INTEGER,
    registered_at TIMESTAMP,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    minted_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE hec_certificates
    ADD COLUMN IF NOT EXISTS certificate_json JSONB,
    ADD COLUMN IF NOT EXISTS ipfs_json_cid VARCHAR(100),
    ADD COLUMN IF NOT EXISTS ipfs_pdf_cid VARCHAR(100),
    ADD COLUMN IF NOT EXISTS ipfs_provider VARCHAR(20),
    ADD COLUMN IF NOT EXISTS registry_tx_hash VARCHAR(66),
    ADD COLUMN IF NOT EXISTS registry_block INTEGER,
    ADD COLUMN IF NOT EXISTS registered_at TIMESTAMP;

ALTER TABLE hec_certificates
    ALTER COLUMN status SET DEFAULT 'pending';

CREATE UNIQUE INDEX IF NOT EXISTS idx_hec_registry_tx_hash
    ON hec_certificates (registry_tx_hash)
    WHERE registry_tx_hash IS NOT NULL;

CREATE TABLE IF NOT EXISTS used_nonces (
    nonce VARCHAR(64) PRIMARY KEY,
    plant_id UUID NOT NULL,
    used_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_used_nonces_used_at ON used_nonces (used_at);

CREATE TABLE IF NOT EXISTS transactions (
    tx_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    buyer_id UUID NOT NULL REFERENCES users(user_id),
    lot_id UUID NOT NULL REFERENCES hec_lots(lot_id),
    quantity INTEGER NOT NULL,
    energy_kwh NUMERIC(16, 4) NOT NULL,
    unit_price_brl NUMERIC(10, 4) NOT NULL,
    total_price_brl NUMERIC(16, 2) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'completed',
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_transactions_buyer_id ON transactions (buyer_id);
CREATE INDEX IF NOT EXISTS ix_transactions_lot_id ON transactions (lot_id);

CREATE TABLE IF NOT EXISTS burn_certificates (
    burn_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(user_id),
    quantity INTEGER NOT NULL,
    energy_kwh NUMERIC(16, 4) NOT NULL,
    certificate_json JSONB,
    hash_sha256 VARCHAR(64) NOT NULL UNIQUE,
    ipfs_json_cid VARCHAR(100),
    ipfs_pdf_cid VARCHAR(100),
    ipfs_provider VARCHAR(20),
    registry_tx_hash VARCHAR(66),
    registry_block INTEGER,
    contract_address VARCHAR(42),
    chain VARCHAR(20) DEFAULT 'polygon-amoy',
    burned_hec_ids JSONB,
    status VARCHAR(20) NOT NULL DEFAULT 'burned',
    reason TEXT,
    burned_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_burn_certificates_user_id ON burn_certificates (user_id);

-- -----------------------------------------------------------------------------
-- 3) Generator onboarding (Alembic 013)
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS generator_profiles (
    profile_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE REFERENCES users(user_id),
    person_type VARCHAR(2) NOT NULL,
    document_id VARCHAR(32) NOT NULL UNIQUE,
    legal_name VARCHAR(255),
    trade_name VARCHAR(255),
    phone VARCHAR(30),
    attribute_assignment_accepted BOOLEAN NOT NULL DEFAULT FALSE,
    assignment_accepted_at TIMESTAMP,
    onboarding_status VARCHAR(30) NOT NULL DEFAULT 'draft',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NULL
);
CREATE INDEX IF NOT EXISTS ix_generator_profiles_user_id ON generator_profiles (user_id);

CREATE TABLE IF NOT EXISTS generator_inverter_connections (
    connection_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id UUID NOT NULL REFERENCES generator_profiles(profile_id),
    plant_id UUID NULL REFERENCES plants(plant_id),
    provider_name VARCHAR(100) NOT NULL,
    integration_mode VARCHAR(30) NOT NULL,
    external_account_ref VARCHAR(255),
    inverter_serial VARCHAR(100),
    consent_accepted BOOLEAN NOT NULL DEFAULT FALSE,
    consented_at TIMESTAMP,
    connection_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    last_sync_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NULL
);
CREATE INDEX IF NOT EXISTS ix_generator_inverter_connections_profile_id
    ON generator_inverter_connections (profile_id);
CREATE INDEX IF NOT EXISTS ix_generator_inverter_connections_plant_id
    ON generator_inverter_connections (plant_id);

-- -----------------------------------------------------------------------------
-- 4) Consumer PF, roles, achievements, dNFT, reward ledger (014-019)
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS consumer_profiles (
    profile_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE REFERENCES users(user_id),
    person_type VARCHAR(2) NOT NULL DEFAULT 'PF',
    document_id VARCHAR(32) UNIQUE,
    display_name VARCHAR(255),
    avatar_seed VARCHAR(20) NOT NULL DEFAULT 'SOA',
    plan_name VARCHAR(60) NOT NULL DEFAULT 'Verde',
    premmia_id VARCHAR(50) UNIQUE,
    premmia_points INTEGER NOT NULL DEFAULT 0,
    current_streak_days INTEGER NOT NULL DEFAULT 0,
    total_retired_mhec INTEGER NOT NULL DEFAULT 0,
    total_co2_avoided_tons NUMERIC(12, 4) NOT NULL DEFAULT 0,
    total_trees_equivalent INTEGER NOT NULL DEFAULT 0,
    total_referrals INTEGER NOT NULL DEFAULT 0,
    joined_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NULL
);
CREATE INDEX IF NOT EXISTS ix_consumer_profiles_user_id ON consumer_profiles (user_id);

CREATE TABLE IF NOT EXISTS user_role_bindings (
    binding_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(user_id),
    role_code VARCHAR(30) NOT NULL,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_user_role_binding UNIQUE (user_id, role_code)
);
CREATE INDEX IF NOT EXISTS ix_user_role_bindings_user_id ON user_role_bindings (user_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_user_role_binding'
    ) THEN
        ALTER TABLE user_role_bindings
            ADD CONSTRAINT uq_user_role_binding UNIQUE (user_id, role_code);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS achievement_catalog (
    achievement_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(60) NOT NULL UNIQUE,
    name VARCHAR(120) NOT NULL,
    description TEXT NOT NULL,
    icon VARCHAR(16) NOT NULL DEFAULT '*',
    metric_key VARCHAR(50) NOT NULL DEFAULT 'total_retired_mhec',
    target_value INTEGER NOT NULL DEFAULT 1,
    points_reward INTEGER NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INTEGER NOT NULL DEFAULT 100,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_achievement_catalog_code ON achievement_catalog (code);

CREATE TABLE IF NOT EXISTS user_achievements (
    user_achievement_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(user_id),
    achievement_id UUID NOT NULL REFERENCES achievement_catalog(achievement_id),
    progress_value INTEGER NOT NULL DEFAULT 0,
    is_unlocked BOOLEAN NOT NULL DEFAULT FALSE,
    unlocked_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NULL,
    CONSTRAINT uq_user_achievement UNIQUE (user_id, achievement_id)
);
CREATE INDEX IF NOT EXISTS ix_user_achievements_user_id ON user_achievements (user_id);
CREATE INDEX IF NOT EXISTS ix_user_achievements_achievement_id ON user_achievements (achievement_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_user_achievement'
    ) THEN
        ALTER TABLE user_achievements
            ADD CONSTRAINT uq_user_achievement UNIQUE (user_id, achievement_id);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS dnft_definitions (
    dnft_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tier_level INTEGER NOT NULL UNIQUE,
    tier_name VARCHAR(120) NOT NULL,
    min_mhec_required INTEGER NOT NULL,
    icon VARCHAR(16) NOT NULL DEFAULT '*',
    benefits_json JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_dnft_states (
    state_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE REFERENCES users(user_id),
    current_tier_level INTEGER NOT NULL DEFAULT 1,
    current_xp_mhec INTEGER NOT NULL DEFAULT 0,
    next_tier_level INTEGER NOT NULL DEFAULT 1,
    next_tier_target_mhec INTEGER NOT NULL DEFAULT 0,
    progress_pct NUMERIC(5, 2) NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NULL
);
CREATE INDEX IF NOT EXISTS ix_user_dnft_states_user_id ON user_dnft_states (user_id);

CREATE TABLE IF NOT EXISTS user_dnft_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(user_id),
    from_tier_level INTEGER,
    to_tier_level INTEGER NOT NULL,
    event_type VARCHAR(30) NOT NULL DEFAULT 'upgrade',
    event_payload JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_user_dnft_events_user_id ON user_dnft_events (user_id);
CREATE INDEX IF NOT EXISTS ix_user_dnft_events_created_at ON user_dnft_events (created_at);

CREATE TABLE IF NOT EXISTS consumer_reward_ledger (
    ledger_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(user_id),
    source_type VARCHAR(30) NOT NULL DEFAULT 'manual',
    source_ref VARCHAR(100),
    points_delta INTEGER NOT NULL DEFAULT 0,
    mhec_delta INTEGER NOT NULL DEFAULT 0,
    balance_after INTEGER,
    description TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_consumer_reward_ledger_user_id ON consumer_reward_ledger (user_id);
CREATE INDEX IF NOT EXISTS ix_consumer_reward_ledger_created_at ON consumer_reward_ledger (created_at);

CREATE TABLE IF NOT EXISTS consumer_dashboard_snapshots (
    snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(user_id),
    reference_month VARCHAR(7) NOT NULL,
    consumed_kwh NUMERIC(12, 3) NOT NULL DEFAULT 0,
    retired_mhec INTEGER NOT NULL DEFAULT 0,
    retirement_pct NUMERIC(5, 2) NOT NULL DEFAULT 0,
    co2_avoided_tons NUMERIC(10, 4) NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_consumer_snapshot_user_month UNIQUE (user_id, reference_month)
);
CREATE INDEX IF NOT EXISTS ix_consumer_dashboard_snapshots_user_id
    ON consumer_dashboard_snapshots (user_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_consumer_snapshot_user_month'
    ) THEN
        ALTER TABLE consumer_dashboard_snapshots
            ADD CONSTRAINT uq_consumer_snapshot_user_month
            UNIQUE (user_id, reference_month);
    END IF;
END $$;

-- -----------------------------------------------------------------------------
-- 5) Seed data (same semantic as Alembic 016/017)
-- -----------------------------------------------------------------------------

INSERT INTO achievement_catalog
    (achievement_id, code, name, description, icon, metric_key, target_value, points_reward, is_active, sort_order, created_at)
VALUES
    (gen_random_uuid(), 'FIRST_RETIREMENT', 'Primeira Aposentadoria', 'Aposentou seu primeiro mHEC', 'seed', 'total_retired_mhec', 1, 100, TRUE, 10, NOW()),
    (gen_random_uuid(), 'STREAK_7', 'Streak 7 dias', 'Compensou consumo por 7 dias seguidos', 'flame', 'current_streak_days', 7, 120, TRUE, 20, NOW()),
    (gen_random_uuid(), 'STREAK_14', 'Streak 14 dias', 'Compensou consumo por 14 dias seguidos', 'flame', 'current_streak_days', 14, 200, TRUE, 30, NOW()),
    (gen_random_uuid(), 'RETIRE_100', '100 mHECs', 'Aposentou 100 mHECs no total', 'tree', 'total_retired_mhec', 100, 180, TRUE, 40, NOW()),
    (gen_random_uuid(), 'RETIRE_300', '300 mHECs', 'Atingiu nivel Bosque', 'forest', 'total_retired_mhec', 300, 260, TRUE, 50, NOW()),
    (gen_random_uuid(), 'RETIRE_500', '500 mHECs', 'Evoluir para Floresta', 'forest', 'total_retired_mhec', 500, 320, TRUE, 60, NOW()),
    (gen_random_uuid(), 'RETIRE_1000', '1000 mHECs', 'Equivalente a 1 MWh limpo', 'bolt', 'total_retired_mhec', 1000, 500, TRUE, 70, NOW()),
    (gen_random_uuid(), 'CARBON_ZERO_MONTH', 'Carbono Zero Mes', 'Registrou 100% de compensacao no mes', 'planet', 'carbon_zero_months', 1, 300, TRUE, 80, NOW()),
    (gen_random_uuid(), 'REFER_5_FRIENDS', 'Indicou 5 amigos', 'Convidou 5 amigos para o ecossistema', 'team', 'total_referrals', 5, 220, TRUE, 90, NOW())
ON CONFLICT (code) DO NOTHING;

INSERT INTO dnft_definitions
    (dnft_id, tier_level, tier_name, min_mhec_required, icon, benefits_json, created_at)
VALUES
    (gen_random_uuid(), 1, 'Semente', 0, 'seed', '["Bem-vindo ao ecossistema SOA/SOS"]'::jsonb, NOW()),
    (gen_random_uuid(), 3, 'Broto', 50, 'sprout', '["Selo de progressao inicial"]'::jsonb, NOW()),
    (gen_random_uuid(), 5, 'Arbusto', 150, 'flower', '["Desconto inicial em compensacoes"]'::jsonb, NOW()),
    (gen_random_uuid(), 7, 'Bosque', 300, 'tree', '["12% desconto compensacao", "Badge verificado", "Relatorio mensal"]'::jsonb, NOW()),
    (gen_random_uuid(), 10, 'Floresta', 500, 'forest', '["Relatorio ESG detalhado", "Beneficios premium"]'::jsonb, NOW()),
    (gen_random_uuid(), 15, 'Bioma', 1500, 'planet', '["Acesso prioritario a pools regionais", "Reconhecimento comunidade"]'::jsonb, NOW())
ON CONFLICT (tier_level) DO NOTHING;

-- =============================================================================
-- End of file
-- =============================================================================


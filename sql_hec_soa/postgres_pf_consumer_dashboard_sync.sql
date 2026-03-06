-- =============================================================================
-- postgres_pf_consumer_dashboard_sync.sql
-- Purpose : Ensure PF consumer dashboard tables exist (no Alembic required)
-- Target  : PostgreSQL (DATABASE_URL / validation_engine)
-- Notes   :
--   1) Safe to rerun (idempotent)
--   2) Does not require TimescaleDB/PostGIS extensions
--   3) Requires base marketplace tables (users) to already exist
-- =============================================================================

DO $$
BEGIN
    IF to_regclass('public.users') IS NULL THEN
        RAISE EXCEPTION 'Tabela users nao encontrada. Rode antes postgres_ecotrack_alembic_sync.sql (ou schema base equivalente).';
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS consumer_profiles (
    profile_id UUID PRIMARY KEY,
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
    binding_id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(user_id),
    role_code VARCHAR(30) NOT NULL,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_user_role_binding UNIQUE (user_id, role_code)
);
CREATE INDEX IF NOT EXISTS ix_user_role_bindings_user_id ON user_role_bindings (user_id);

CREATE TABLE IF NOT EXISTS achievement_catalog (
    achievement_id UUID PRIMARY KEY,
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
    user_achievement_id UUID PRIMARY KEY,
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

CREATE TABLE IF NOT EXISTS dnft_definitions (
    dnft_id UUID PRIMARY KEY,
    tier_level INTEGER NOT NULL UNIQUE,
    tier_name VARCHAR(120) NOT NULL,
    min_mhec_required INTEGER NOT NULL,
    icon VARCHAR(16) NOT NULL DEFAULT '*',
    benefits_json JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_dnft_states (
    state_id UUID PRIMARY KEY,
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
    event_id UUID PRIMARY KEY,
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
    ledger_id UUID PRIMARY KEY,
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
    snapshot_id UUID PRIMARY KEY,
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

-- Achievement seed (deterministic UUIDs)
INSERT INTO achievement_catalog
    (achievement_id, code, name, description, icon, metric_key, target_value, points_reward, is_active, sort_order, created_at)
VALUES
    ('10000000-0000-0000-0000-000000000001', 'FIRST_RETIREMENT', 'Primeira Aposentadoria', 'Aposentou seu primeiro mHEC', 'seed', 'total_retired_mhec', 1, 100, TRUE, 10, NOW()),
    ('10000000-0000-0000-0000-000000000002', 'STREAK_7', 'Streak 7 dias', 'Compensou consumo por 7 dias seguidos', 'flame', 'current_streak_days', 7, 120, TRUE, 20, NOW()),
    ('10000000-0000-0000-0000-000000000003', 'STREAK_14', 'Streak 14 dias', 'Compensou consumo por 14 dias seguidos', 'flame', 'current_streak_days', 14, 200, TRUE, 30, NOW()),
    ('10000000-0000-0000-0000-000000000004', 'RETIRE_100', '100 mHECs', 'Aposentou 100 mHECs no total', 'tree', 'total_retired_mhec', 100, 180, TRUE, 40, NOW()),
    ('10000000-0000-0000-0000-000000000005', 'RETIRE_300', '300 mHECs', 'Atingiu nivel Bosque', 'forest', 'total_retired_mhec', 300, 260, TRUE, 50, NOW()),
    ('10000000-0000-0000-0000-000000000006', 'RETIRE_500', '500 mHECs', 'Evoluir para Floresta', 'forest', 'total_retired_mhec', 500, 320, TRUE, 60, NOW()),
    ('10000000-0000-0000-0000-000000000007', 'RETIRE_1000', '1000 mHECs', 'Equivalente a 1 MWh limpo', 'bolt', 'total_retired_mhec', 1000, 500, TRUE, 70, NOW()),
    ('10000000-0000-0000-0000-000000000008', 'CARBON_ZERO_MONTH', 'Carbono Zero Mes', 'Registrou 100% de compensacao no mes', 'planet', 'carbon_zero_months', 1, 300, TRUE, 80, NOW()),
    ('10000000-0000-0000-0000-000000000009', 'REFER_5_FRIENDS', 'Indicou 5 amigos', 'Convidou 5 amigos para o ecossistema', 'team', 'total_referrals', 5, 220, TRUE, 90, NOW())
ON CONFLICT (code) DO NOTHING;

-- dNFT seed (deterministic UUIDs)
INSERT INTO dnft_definitions
    (dnft_id, tier_level, tier_name, min_mhec_required, icon, benefits_json, created_at)
VALUES
    ('20000000-0000-0000-0000-000000000001', 1, 'Semente', 0, 'seed', '["Bem-vindo ao ecossistema SOA/SOS"]'::jsonb, NOW()),
    ('20000000-0000-0000-0000-000000000002', 3, 'Broto', 50, 'sprout', '["Selo de progressao inicial"]'::jsonb, NOW()),
    ('20000000-0000-0000-0000-000000000003', 5, 'Arbusto', 150, 'flower', '["Desconto inicial em compensacoes"]'::jsonb, NOW()),
    ('20000000-0000-0000-0000-000000000004', 7, 'Bosque', 300, 'tree', '["12% desconto compensacao", "Badge verificado", "Relatorio mensal"]'::jsonb, NOW()),
    ('20000000-0000-0000-0000-000000000005', 10, 'Floresta', 500, 'forest', '["Relatorio ESG detalhado", "Beneficios premium"]'::jsonb, NOW()),
    ('20000000-0000-0000-0000-000000000006', 15, 'Bioma', 1500, 'planet', '["Acesso prioritario a pools regionais", "Reconhecimento comunidade"]'::jsonb, NOW())
ON CONFLICT (tier_level) DO NOTHING;

-- Optional compatibility: ensure burn timestamp exists for dashboard sync from burns
DO $$
BEGIN
    IF to_regclass('public.burn_certificates') IS NOT NULL THEN
        ALTER TABLE burn_certificates
            ADD COLUMN IF NOT EXISTS burned_at TIMESTAMP NOT NULL DEFAULT NOW();
    END IF;
END $$;

-- =============================================================================
-- End
-- =============================================================================

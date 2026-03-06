ALTER TABLE plants
ADD COLUMN owner_user_id UUID;

ALTER TABLE plants
ADD CONSTRAINT fk_plants_owner_user_id_users
FOREIGN KEY (owner_user_id)
REFERENCES users(user_id);

CREATE INDEX ix_plants_owner_user_id
ON plants(owner_user_id);


CREATE TABLE generator_profiles (
    profile_id UUID PRIMARY KEY,
    user_id UUID NOT NULL UNIQUE,
    person_type VARCHAR(2) NOT NULL,
    document_id VARCHAR(32) NOT NULL UNIQUE,
    legal_name VARCHAR(255),
    trade_name VARCHAR(255),
    phone VARCHAR(30),
    attribute_assignment_accepted BOOLEAN NOT NULL DEFAULT false,
    assignment_accepted_at TIMESTAMP,
    onboarding_status VARCHAR(30) NOT NULL DEFAULT 'draft',
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP,

    CONSTRAINT fk_generator_profiles_user
    FOREIGN KEY (user_id)
    REFERENCES users(user_id)
);

CREATE INDEX ix_generator_profiles_user_id
ON generator_profiles(user_id);


CREATE TABLE generator_inverter_connections (
    connection_id UUID PRIMARY KEY,
    profile_id UUID NOT NULL,
    plant_id UUID,
    provider_name VARCHAR(100) NOT NULL,
    integration_mode VARCHAR(30) NOT NULL,
    external_account_ref VARCHAR(255),
    inverter_serial VARCHAR(100),
    consent_accepted BOOLEAN NOT NULL DEFAULT false,
    consented_at TIMESTAMP,
    connection_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    last_sync_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP,

    FOREIGN KEY (profile_id)
    REFERENCES generator_profiles(profile_id),

    FOREIGN KEY (plant_id)
    REFERENCES plants(plant_id)
);

CREATE INDEX ix_generator_inverter_connections_profile_id
ON generator_inverter_connections(profile_id);

CREATE INDEX ix_generator_inverter_connections_plant_id
ON generator_inverter_connections(plant_id);

CREATE TABLE consumer_profiles (
    profile_id UUID PRIMARY KEY,
    user_id UUID NOT NULL UNIQUE,
    person_type VARCHAR(2) NOT NULL DEFAULT 'PF',
    document_id VARCHAR(32) UNIQUE,
    display_name VARCHAR(255),
    avatar_seed VARCHAR(20) NOT NULL DEFAULT 'SOA',
    plan_name VARCHAR(60) NOT NULL DEFAULT 'Verde',
    premmia_id VARCHAR(50) UNIQUE,
    premmia_points INTEGER NOT NULL DEFAULT 0,
    current_streak_days INTEGER NOT NULL DEFAULT 0,
    total_retired_mhec INTEGER NOT NULL DEFAULT 0,
    total_co2_avoided_tons NUMERIC(12,4) NOT NULL DEFAULT 0,
    total_trees_equivalent INTEGER NOT NULL DEFAULT 0,
    total_referrals INTEGER NOT NULL DEFAULT 0,
    joined_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP,

    FOREIGN KEY (user_id)
    REFERENCES users(user_id)
);

CREATE INDEX ix_consumer_profiles_user_id
ON consumer_profiles(user_id);

CREATE TABLE user_role_bindings (
    binding_id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    role_code VARCHAR(30) NOT NULL,
    is_primary BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMP NOT NULL,

    CONSTRAINT uq_user_role_binding
    UNIQUE (user_id, role_code),

    FOREIGN KEY (user_id)
    REFERENCES users(user_id)
);

CREATE INDEX ix_user_role_bindings_user_id
ON user_role_bindings(user_id);

CREATE TABLE achievement_catalog (
    achievement_id UUID PRIMARY KEY,
    code VARCHAR(60) NOT NULL UNIQUE,
    name VARCHAR(120) NOT NULL,
    description TEXT NOT NULL,
    icon VARCHAR(16) NOT NULL DEFAULT '*',
    metric_key VARCHAR(50) NOT NULL DEFAULT 'total_retired_mhec',
    target_value INTEGER NOT NULL DEFAULT 1,
    points_reward INTEGER NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT true,
    sort_order INTEGER NOT NULL DEFAULT 100,
    created_at TIMESTAMP NOT NULL
);

CREATE INDEX ix_achievement_catalog_code
ON achievement_catalog(code);


CREATE TABLE user_achievements (
    user_achievement_id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    achievement_id UUID NOT NULL,
    progress_value INTEGER NOT NULL DEFAULT 0,
    is_unlocked BOOLEAN NOT NULL DEFAULT false,
    unlocked_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP,

    CONSTRAINT uq_user_achievement
    UNIQUE(user_id, achievement_id),

    FOREIGN KEY (user_id)
    REFERENCES users(user_id),

    FOREIGN KEY (achievement_id)
    REFERENCES achievement_catalog(achievement_id)
);

CREATE INDEX ix_user_achievements_user_id
ON user_achievements(user_id);

CREATE INDEX ix_user_achievements_achievement_id
ON user_achievements(achievement_id);

CREATE TABLE dnft_definitions (
    dnft_id UUID PRIMARY KEY,
    tier_level INTEGER NOT NULL UNIQUE,
    tier_name VARCHAR(120) NOT NULL,
    min_mhec_required INTEGER NOT NULL,
    icon VARCHAR(16) NOT NULL DEFAULT '*',
    benefits_json JSONB,
    created_at TIMESTAMP NOT NULL
);


CREATE TABLE user_dnft_states (
    state_id UUID PRIMARY KEY,
    user_id UUID NOT NULL UNIQUE,
    current_tier_level INTEGER NOT NULL DEFAULT 1,
    current_xp_mhec INTEGER NOT NULL DEFAULT 0,
    next_tier_level INTEGER NOT NULL DEFAULT 1,
    next_tier_target_mhec INTEGER NOT NULL DEFAULT 0,
    progress_pct NUMERIC(5,2) NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP,

    FOREIGN KEY (user_id)
    REFERENCES users(user_id)
);

CREATE INDEX ix_user_dnft_states_user_id
ON user_dnft_states(user_id);


CREATE TABLE user_dnft_events (
    event_id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    from_tier_level INTEGER,
    to_tier_level INTEGER NOT NULL,
    event_type VARCHAR(30) NOT NULL DEFAULT 'upgrade',
    event_payload JSONB,
    created_at TIMESTAMP NOT NULL,

    FOREIGN KEY (user_id)
    REFERENCES users(user_id)
);

CREATE INDEX ix_user_dnft_events_user_id
ON user_dnft_events(user_id);

CREATE INDEX ix_user_dnft_events_created_at
ON user_dnft_events(created_at);

CREATE TABLE consumer_reward_ledger (
    ledger_id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    source_type VARCHAR(30) NOT NULL DEFAULT 'manual',
    source_ref VARCHAR(100),
    points_delta INTEGER NOT NULL DEFAULT 0,
    mhec_delta INTEGER NOT NULL DEFAULT 0,
    balance_after INTEGER,
    description TEXT,
    created_at TIMESTAMP NOT NULL,

    FOREIGN KEY (user_id)
    REFERENCES users(user_id)
);

CREATE INDEX ix_consumer_reward_ledger_user_id
ON consumer_reward_ledger(user_id);

CREATE INDEX ix_consumer_reward_ledger_created_at
ON consumer_reward_ledger(created_at);

CREATE TABLE consumer_dashboard_snapshots (
    snapshot_id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    reference_month VARCHAR(7) NOT NULL,
    consumed_kwh NUMERIC(12,3) NOT NULL DEFAULT 0,
    retired_mhec INTEGER NOT NULL DEFAULT 0,
    retirement_pct NUMERIC(5,2) NOT NULL DEFAULT 0,
    co2_avoided_tons NUMERIC(10,4) NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL,

    CONSTRAINT uq_consumer_snapshot_user_month
    UNIQUE (user_id, reference_month),

    FOREIGN KEY (user_id)
    REFERENCES users(user_id)
);

CREATE INDEX ix_consumer_dashboard_snapshots_user_id
ON consumer_dashboard_snapshots(user_id);

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

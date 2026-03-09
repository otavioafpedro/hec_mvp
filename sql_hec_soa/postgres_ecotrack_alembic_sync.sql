-- =============================================================================
-- postgres_pj_institutional_profile_sync.sql
-- Purpose : Fix legacy consumer profile person_type for PJ institutional users
-- Target  : PostgreSQL
-- Notes   :
--   1) Safe to rerun (idempotent)
--   2) No TimescaleDB/PostGIS extension required
--   3) Run after postgres_pf_consumer_dashboard_sync.sql
-- =============================================================================

DO $$
BEGIN
    IF to_regclass('public.consumer_profiles') IS NULL THEN
        RAISE EXCEPTION 'Tabela consumer_profiles nao encontrada. Rode antes postgres_pf_consumer_dashboard_sync.sql.';
    END IF;
END $$;

-- 1) Trust generator profile first (authoritative PF/PJ declaration).
DO $$
BEGIN
    IF to_regclass('public.generator_profiles') IS NOT NULL THEN
        UPDATE consumer_profiles cp
        SET
            person_type = gp.person_type,
            updated_at = NOW()
        FROM generator_profiles gp
        WHERE cp.user_id = gp.user_id
          AND gp.person_type IN ('PF', 'PJ')
          AND cp.person_type IS DISTINCT FROM gp.person_type;
    END IF;
END $$;

-- 2) Fallback for institutional role bindings.
DO $$
BEGIN
    IF to_regclass('public.user_role_bindings') IS NOT NULL THEN
        UPDATE consumer_profiles cp
        SET
            person_type = 'PJ',
            updated_at = NOW()
        FROM user_role_bindings urb
        WHERE cp.user_id = urb.user_id
          AND lower(coalesce(urb.role_code, '')) IN ('pj', 'institutional', 'corporate', 'company')
          AND cp.person_type IS DISTINCT FROM 'PJ';
    END IF;
END $$;

-- 3) Last fallback from users.role text.
DO $$
BEGIN
    IF to_regclass('public.users') IS NOT NULL THEN
        UPDATE consumer_profiles cp
        SET
            person_type = 'PJ',
            updated_at = NOW()
        FROM users u
        WHERE cp.user_id = u.user_id
          AND (
              lower(coalesce(u.role, '')) LIKE '%pj%'
              OR lower(coalesce(u.role, '')) LIKE '%institutional%'
              OR lower(coalesce(u.role, '')) LIKE '%corporate%'
              OR lower(coalesce(u.role, '')) LIKE '%company%'
          )
          AND cp.person_type IS DISTINCT FROM 'PJ';
    END IF;
END $$;

-- 4) Normalize invalid/null values to PF (conservative default).
UPDATE consumer_profiles
SET
    person_type = 'PF',
    updated_at = NOW()
WHERE person_type IS NULL
   OR upper(person_type) NOT IN ('PF', 'PJ');

-- 5) Force canonical uppercase.
UPDATE consumer_profiles
SET
    person_type = upper(person_type),
    updated_at = NOW()
WHERE person_type IN ('pf', 'pj');

-- =============================================================================
-- End
-- =============================================================================


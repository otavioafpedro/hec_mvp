-- =============================================================================
-- ecotrack_mysql_compat_sync.sql
-- Purpose: Compatibility patch for SOA MySQL schema used alongside ecotrack
-- Target : MariaDB/MySQL transactional DB (soa_sos)
-- Notes  :
--   1) Run AFTER sql_hec_soa/mysql_schema.sql
--   2) Safe to rerun
-- =============================================================================

-- Ensure minimum columns consumed by /soa/v1/inverter-telemetry
ALTER TABLE sites
    ADD COLUMN IF NOT EXISTS status ENUM('pending','active','maintenance','decommissioned') NOT NULL DEFAULT 'pending';

ALTER TABLE devices
    ADD COLUMN IF NOT EXISTS device_type ENUM('inverter','meter','sensor','gateway') NOT NULL,
    ADD COLUMN IF NOT EXISTS status ENUM('online','offline','maintenance','retired') NOT NULL DEFAULT 'offline',
    ADD COLUMN IF NOT EXISTS last_seen_at DATETIME NULL,
    ADD COLUMN IF NOT EXISTS updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;

-- Add operational indexes if missing (idempotent)
SET @idx_exists := (
    SELECT COUNT(1)
    FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'sites'
      AND index_name = 'idx_sites_status'
);
SET @sql := IF(@idx_exists = 0,
    'CREATE INDEX idx_sites_status ON sites (status)',
    'SELECT ''idx_sites_status exists''');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @idx_exists := (
    SELECT COUNT(1)
    FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'devices'
      AND index_name = 'idx_devices_type_status'
);
SET @sql := IF(@idx_exists = 0,
    'CREATE INDEX idx_devices_type_status ON devices (device_type, status)',
    'SELECT ''idx_devices_type_status exists''');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @idx_exists := (
    SELECT COUNT(1)
    FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'devices'
      AND index_name = 'idx_devices_last_seen'
);
SET @sql := IF(@idx_exists = 0,
    'CREATE INDEX idx_devices_last_seen ON devices (last_seen_at)',
    'SELECT ''idx_devices_last_seen exists''');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- =============================================================================
-- End of file
-- =============================================================================

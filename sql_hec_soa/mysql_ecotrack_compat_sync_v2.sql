-- =============================================================================
-- ecotrack_mysql_compat_sync_v2.sql
-- Purpose: Compatibility patch for SOA MySQL schema used alongside ecotrack
-- Target : MariaDB/MySQL transactional DB (soa_sos)
-- Notes  :
--   1) Run AFTER sql_hec_soa/mysql_schema.sql
--   2) Safe to rerun
--   3) Compatible with servers that do NOT support ADD COLUMN IF NOT EXISTS
-- =============================================================================

-- sites.status
SET @col_exists := (
    SELECT COUNT(1)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'sites'
      AND column_name = 'status'
);
SET @sql := IF(
    @col_exists = 0,
    'ALTER TABLE sites ADD COLUMN status ENUM(''pending'',''active'',''maintenance'',''decommissioned'') NOT NULL DEFAULT ''pending''',
    'SELECT ''sites.status exists'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- devices.device_type
SET @col_exists := (
    SELECT COUNT(1)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'devices'
      AND column_name = 'device_type'
);
SET @sql := IF(
    @col_exists = 0,
    'ALTER TABLE devices ADD COLUMN device_type ENUM(''inverter'',''meter'',''sensor'',''gateway'') NOT NULL',
    'SELECT ''devices.device_type exists'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- devices.status
SET @col_exists := (
    SELECT COUNT(1)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'devices'
      AND column_name = 'status'
);
SET @sql := IF(
    @col_exists = 0,
    'ALTER TABLE devices ADD COLUMN status ENUM(''online'',''offline'',''maintenance'',''retired'') NOT NULL DEFAULT ''offline''',
    'SELECT ''devices.status exists'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- devices.last_seen_at
SET @col_exists := (
    SELECT COUNT(1)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'devices'
      AND column_name = 'last_seen_at'
);
SET @sql := IF(
    @col_exists = 0,
    'ALTER TABLE devices ADD COLUMN last_seen_at DATETIME NULL',
    'SELECT ''devices.last_seen_at exists'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- devices.updated_at
SET @col_exists := (
    SELECT COUNT(1)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'devices'
      AND column_name = 'updated_at'
);
SET @sql := IF(
    @col_exists = 0,
    'ALTER TABLE devices ADD COLUMN updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP',
    'SELECT ''devices.updated_at exists'''
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- indexes
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

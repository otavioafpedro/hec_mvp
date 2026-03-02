-- Minimal seed to test /soa/v1/inverter-telemetry end-to-end
-- Runs only on first MariaDB container initialization.

SET NAMES utf8mb4;

SET @org_uuid  := '00000000-0000-0000-0000-000000000101';
SET @site_uuid := '00000000-0000-0000-0000-000000000201';
SET @dev_uuid  := '00000000-0000-0000-0000-000000000301';

INSERT INTO organizations (uuid, name, legal_name, tax_id, country_code, tier, status)
VALUES (@org_uuid, 'Solar One Demo Org', 'Solar One Demo Org LTDA', '00000000000191', 'BR', 'standard', 'active')
ON DUPLICATE KEY UPDATE updated_at = CURRENT_TIMESTAMP;

SELECT id INTO @org_id FROM organizations WHERE uuid = @org_uuid LIMIT 1;

INSERT INTO sites (
    uuid,
    org_id,
    name,
    site_type,
    capacity_kw,
    latitude,
    longitude,
    h3_index_res7,
    timezone,
    city,
    state_code,
    country_code,
    status
) VALUES (
    @site_uuid,
    @org_id,
    'Usina Demo Sao Paulo',
    'solar_pv',
    75.000,
    -23.5505200,
    -46.6333080,
    '87a8101ffffffff',
    'America/Sao_Paulo',
    'Sao Paulo',
    'SP',
    'BR',
    'active'
)
ON DUPLICATE KEY UPDATE updated_at = CURRENT_TIMESTAMP;

SELECT id INTO @site_id FROM sites WHERE uuid = @site_uuid LIMIT 1;

INSERT INTO devices (
    uuid,
    site_id,
    device_type,
    manufacturer,
    model,
    serial_number,
    firmware_ver,
    comm_protocol,
    rated_power_kw,
    status
) VALUES (
    @dev_uuid,
    @site_id,
    'inverter',
    'Growatt',
    'MIN 6000TL-X',
    'DEMO-INV-0001',
    '1.0.0',
    'api_rest',
    6.000,
    'online'
)
ON DUPLICATE KEY UPDATE updated_at = CURRENT_TIMESTAMP;

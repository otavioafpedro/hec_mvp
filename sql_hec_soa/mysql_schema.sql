-- =============================================================================
-- SOA/SOS — HEC/H-REC  ·  MySQL (MariaDB 10.11+) Transactional Schema
-- =============================================================================
-- Versão:  1.0  ·  2026-02-26
-- Projeto: Solar One Account / Sustainability Operating System
-- Escopo:  Dados transacionais e de negócio (multi-org B2B + B2C)
-- =============================================================================

-- CREATE DATABASE IF NOT EXISTS soa_sos
--   CHARACTER SET utf8mb4
--   COLLATE utf8mb4_unicode_ci;
-- USE soa_sos;

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. TENANCY / ORGANIZAÇÕES / USUÁRIOS
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE organizations (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    uuid            CHAR(36)        NOT NULL,
    name            VARCHAR(255)    NOT NULL,
    legal_name      VARCHAR(255)    NULL,
    tax_id          VARCHAR(30)     NULL COMMENT 'CNPJ ou tax id estrangeiro',
    country_code    CHAR(2)         NOT NULL DEFAULT 'BR',
    tier            ENUM('free','standard','granular','premium','enterprise') NOT NULL DEFAULT 'standard',
    plan_started_at DATETIME        NULL,
    plan_expires_at DATETIME        NULL,
    status          ENUM('active','suspended','closed') NOT NULL DEFAULT 'active',
    metadata_json   JSON            NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_org_uuid (uuid),
    UNIQUE KEY uq_org_tax_id (tax_id)
) ENGINE=InnoDB;

CREATE TABLE users (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    uuid            CHAR(36)        NOT NULL,
    org_id          BIGINT UNSIGNED NULL COMMENT 'NULL = B2C individual',
    email           VARCHAR(320)    NOT NULL,
    password_hash   VARCHAR(255)    NOT NULL,
    full_name       VARCHAR(255)    NOT NULL,
    cpf_cnpj        VARCHAR(30)     NULL,
    phone           VARCHAR(30)     NULL,
    country_code    CHAR(2)         NOT NULL DEFAULT 'BR',
    locale          VARCHAR(10)     NOT NULL DEFAULT 'pt-BR',
    role            ENUM('admin','manager','operator','viewer','b2c_user') NOT NULL DEFAULT 'b2c_user',
    email_verified  TINYINT(1)      NOT NULL DEFAULT 0,
    mfa_enabled     TINYINT(1)      NOT NULL DEFAULT 0,
    status          ENUM('active','suspended','closed') NOT NULL DEFAULT 'active',
    last_login_at   DATETIME        NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_user_uuid (uuid),
    UNIQUE KEY uq_user_email (email),
    KEY idx_user_org (org_id),
    CONSTRAINT fk_user_org FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE roles (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    org_id      BIGINT UNSIGNED NOT NULL,
    name        VARCHAR(100)    NOT NULL,
    description VARCHAR(500)    NULL,
    created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_role_org_name (org_id, name),
    CONSTRAINT fk_role_org FOREIGN KEY (org_id) REFERENCES organizations(id)
) ENGINE=InnoDB;

CREATE TABLE permissions (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    code        VARCHAR(100)    NOT NULL COMMENT 'e.g. hec:mint, marketplace:sell',
    description VARCHAR(500)    NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_perm_code (code)
) ENGINE=InnoDB;

CREATE TABLE role_permissions (
    role_id       BIGINT UNSIGNED NOT NULL,
    permission_id BIGINT UNSIGNED NOT NULL,
    PRIMARY KEY (role_id, permission_id),
    CONSTRAINT fk_rp_role FOREIGN KEY (role_id)       REFERENCES roles(id) ON DELETE CASCADE,
    CONSTRAINT fk_rp_perm FOREIGN KEY (permission_id) REFERENCES permissions(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE user_roles (
    user_id BIGINT UNSIGNED NOT NULL,
    role_id BIGINT UNSIGNED NOT NULL,
    PRIMARY KEY (user_id, role_id),
    CONSTRAINT fk_ur_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_ur_role FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. SITES / GERADORES SOLARES
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE sites (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    uuid            CHAR(36)        NOT NULL,
    org_id          BIGINT UNSIGNED NOT NULL,
    name            VARCHAR(255)    NOT NULL,
    site_type       ENUM('solar_pv','wind','biomass','hydro_small','hybrid') NOT NULL DEFAULT 'solar_pv',
    capacity_kw     DECIMAL(12,3)   NOT NULL COMMENT 'potência instalada kWp',
    latitude        DECIMAL(10,7)   NOT NULL,
    longitude       DECIMAL(10,7)   NOT NULL,
    h3_index_res7   VARCHAR(20)     NOT NULL COMMENT 'H3 hex ~5 km²',
    timezone        VARCHAR(50)     NOT NULL DEFAULT 'America/Sao_Paulo',
    address_line    VARCHAR(500)    NULL,
    city            VARCHAR(150)    NULL,
    state_code      VARCHAR(5)      NULL,
    country_code    CHAR(2)         NOT NULL DEFAULT 'BR',
    aneel_code      VARCHAR(30)     NULL COMMENT 'código ANEEL GD',
    commission_date DATE            NULL,
    status          ENUM('pending','active','maintenance','decommissioned') NOT NULL DEFAULT 'pending',
    metadata_json   JSON            NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_site_uuid (uuid),
    KEY idx_site_org (org_id),
    KEY idx_site_h3 (h3_index_res7),
    KEY idx_site_geo (latitude, longitude),
    CONSTRAINT fk_site_org FOREIGN KEY (org_id) REFERENCES organizations(id)
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. DISPOSITIVOS (inversores, medidores, sensores)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE devices (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    uuid            CHAR(36)        NOT NULL,
    site_id         BIGINT UNSIGNED NOT NULL,
    device_type     ENUM('inverter','meter','sensor','gateway') NOT NULL,
    manufacturer    VARCHAR(150)    NULL,
    model           VARCHAR(150)    NULL,
    serial_number   VARCHAR(100)    NULL,
    firmware_ver    VARCHAR(50)     NULL,
    comm_protocol   ENUM('mqtt','modbus_tcp','sunspec','api_rest','api_grpc') NOT NULL DEFAULT 'mqtt',
    mqtt_topic      VARCHAR(255)    NULL,
    rated_power_kw  DECIMAL(10,3)   NULL,
    status          ENUM('online','offline','maintenance','retired') NOT NULL DEFAULT 'offline',
    last_seen_at    DATETIME        NULL,
    metadata_json   JSON            NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_device_uuid (uuid),
    KEY idx_device_site (site_id),
    KEY idx_device_serial (serial_number),
    CONSTRAINT fk_device_site FOREIGN KEY (site_id) REFERENCES sites(id)
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. CONTRATOS / ASSINATURAS
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE contracts (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    uuid                CHAR(36)        NOT NULL,
    org_id              BIGINT UNSIGNED NOT NULL,
    contract_type       ENUM('b2b_standard','b2b_granular','b2b_premium','b2c_subscription','b2c_payg','pilot','license') NOT NULL,
    tier                ENUM('standard','granular','premium') NOT NULL DEFAULT 'standard',
    price_per_mwh_brl   DECIMAL(10,2)   NULL,
    price_per_kwh_brl   DECIMAL(10,4)   NULL,
    auto_recharge       TINYINT(1)      NOT NULL DEFAULT 0,
    recharge_amount_brl DECIMAL(10,2)   NULL,
    start_date          DATE            NOT NULL,
    end_date            DATE            NULL,
    status              ENUM('draft','active','paused','terminated','expired') NOT NULL DEFAULT 'draft',
    signed_at           DATETIME        NULL,
    metadata_json       JSON            NULL,
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_contract_uuid (uuid),
    KEY idx_contract_org (org_id),
    CONSTRAINT fk_contract_org FOREIGN KEY (org_id) REFERENCES organizations(id)
) ENGINE=InnoDB;

CREATE TABLE subscriptions (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    uuid            CHAR(36)        NOT NULL,
    user_id         BIGINT UNSIGNED NOT NULL,
    contract_id     BIGINT UNSIGNED NULL,
    plan            ENUM('leaf_basic','leaf_premium','placa_bronze','placa_silver','placa_gold','placa_diamond','custom') NOT NULL,
    price_brl       DECIMAL(10,2)   NOT NULL,
    billing_cycle   ENUM('monthly','quarterly','annual') NOT NULL DEFAULT 'monthly',
    auto_recharge   TINYINT(1)      NOT NULL DEFAULT 1,
    streak_months   INT UNSIGNED    NOT NULL DEFAULT 0,
    streak_pct      DECIMAL(5,2)    NULL COMMENT 'matching % current period',
    status          ENUM('active','paused','cancelled','expired') NOT NULL DEFAULT 'active',
    started_at      DATETIME        NOT NULL,
    expires_at      DATETIME        NULL,
    cancelled_at    DATETIME        NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_sub_uuid (uuid),
    KEY idx_sub_user (user_id),
    KEY idx_sub_contract (contract_id),
    CONSTRAINT fk_sub_user     FOREIGN KEY (user_id)     REFERENCES users(id),
    CONSTRAINT fk_sub_contract FOREIGN KEY (contract_id) REFERENCES contracts(id)
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. CARTEIRAS (wallets)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE wallets (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    uuid                CHAR(36)        NOT NULL,
    owner_type          ENUM('user','org','system','escrow') NOT NULL,
    owner_id            BIGINT UNSIGNED NOT NULL COMMENT 'user.id ou org.id conforme owner_type',
    blockchain_address  VARCHAR(100)    NULL COMMENT 'endereço Polygon se custodial',
    is_custodial        TINYINT(1)      NOT NULL DEFAULT 1,
    balance_mhec        DECIMAL(18,3)   NOT NULL DEFAULT 0 COMMENT 'saldo mHEC (kWh)',
    balance_hec         DECIMAL(18,3)   NOT NULL DEFAULT 0 COMMENT 'saldo HEC  (MWh)',
    balance_fiat_brl    DECIMAL(14,2)   NOT NULL DEFAULT 0,
    status              ENUM('active','frozen','closed') NOT NULL DEFAULT 'active',
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_wallet_uuid (uuid),
    KEY idx_wallet_owner (owner_type, owner_id)
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. ERC-1155 TOKEN REGISTRY / COLEÇÕES
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE token_definitions (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    token_id_erc    INT UNSIGNED    NOT NULL COMMENT 'ERC-1155 tokenId (#0 mHEC, #1 HEC, #100-104 dNFT, #200-203 Placa)',
    symbol          VARCHAR(30)     NOT NULL,
    name            VARCHAR(150)    NOT NULL,
    token_class     ENUM('fungible','soulbound_nft') NOT NULL,
    unit_energy     ENUM('Wh','kWh','MWh','none') NOT NULL DEFAULT 'kWh',
    is_transferable TINYINT(1)      NOT NULL DEFAULT 1,
    description     VARCHAR(1000)   NULL,
    metadata_uri    VARCHAR(500)    NULL COMMENT 'IPFS URI for metadata',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_token_erc_id (token_id_erc)
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. H3 REGIÕES (cache de hexágonos ativos)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE h3_regions (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    h3_index            VARCHAR(20)     NOT NULL,
    resolution          TINYINT UNSIGNED NOT NULL DEFAULT 7,
    center_lat          DECIMAL(10,7)   NOT NULL,
    center_lng          DECIMAL(10,7)   NOT NULL,
    active_sites_count  INT UNSIGNED    NOT NULL DEFAULT 0,
    country_code        CHAR(2)         NOT NULL DEFAULT 'BR',
    state_code          VARCHAR(5)      NULL,
    status              ENUM('active','inactive') NOT NULL DEFAULT 'active',
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_h3_index (h3_index)
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────────────────────────────────────
-- 8. CERTIFICADOS HEC — LOTES E INTERVALOS
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE certificate_batches (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    uuid                CHAR(36)        NOT NULL,
    batch_date          DATE            NOT NULL COMMENT 'dia de consolidação',
    site_id             BIGINT UNSIGNED NULL COMMENT 'NULL = batch multi-site',
    h3_region_id        BIGINT UNSIGNED NULL,
    total_energy_wh     BIGINT          NOT NULL DEFAULT 0 COMMENT 'energia total do lote em Wh',
    total_certificates  INT UNSIGNED    NOT NULL DEFAULT 0,
    merkle_root_mint    VARCHAR(66)     NULL COMMENT 'SHA-256 hex root da árvore mint',
    ipfs_cid_mint       VARCHAR(100)    NULL COMMENT 'CID das folhas no IPFS',
    tx_hash_mint        VARCHAR(66)     NULL COMMENT 'hash da tx mintBatch() on-chain',
    tx_hash_merkle      VARCHAR(66)     NULL COMMENT 'hash da tx commitMerkleRoot()',
    blockchain_network  VARCHAR(30)     NOT NULL DEFAULT 'polygon',
    block_number        BIGINT UNSIGNED NULL,
    status              ENUM('pending','minted','partially_claimed','fully_claimed','voided') NOT NULL DEFAULT 'pending',
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_batch_uuid (uuid),
    KEY idx_batch_date (batch_date),
    KEY idx_batch_site (site_id),
    KEY idx_batch_region (h3_region_id),
    CONSTRAINT fk_batch_site   FOREIGN KEY (site_id)      REFERENCES sites(id),
    CONSTRAINT fk_batch_region FOREIGN KEY (h3_region_id)  REFERENCES h3_regions(id)
) ENGINE=InnoDB;

CREATE TABLE certificates (
    id                      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    uuid                    CHAR(36)        NOT NULL,
    batch_id                BIGINT UNSIGNED NOT NULL,
    site_id                 BIGINT UNSIGNED NOT NULL,
    device_id               BIGINT UNSIGNED NULL COMMENT 'inverter/meter de referência',
    token_id_erc            INT UNSIGNED    NOT NULL COMMENT '0=mHEC, 1=HEC',
    -- Intervalo de energia
    interval_start          DATETIME        NOT NULL COMMENT 'início do intervalo (UTC)',
    interval_end            DATETIME        NOT NULL COMMENT 'fim do intervalo (UTC)',
    timezone                VARCHAR(50)     NOT NULL DEFAULT 'America/Sao_Paulo',
    energy_generated_wh     BIGINT          NOT NULL COMMENT 'geração bruta (Wh)',
    energy_exported_wh      BIGINT          NOT NULL DEFAULT 0 COMMENT 'exportado ao grid (Wh)',
    energy_self_consumed_wh BIGINT          NOT NULL DEFAULT 0 COMMENT 'autoconsumo (Wh)',
    -- Validação QSV
    qsv_score               TINYINT UNSIGNED NOT NULL COMMENT '0-3',
    qsv_run_id              BIGINT UNSIGNED  NULL,
    p_truth                 DECIMAL(5,4)    NOT NULL DEFAULT 1.0000 COMMENT 'fator de confiança 0-1',
    measurement_method      ENUM('inverter','meter','satellite','hybrid') NOT NULL DEFAULT 'inverter',
    -- Certificação
    tier                    ENUM('standard','granular','premium') NOT NULL DEFAULT 'standard',
    emission_factor_tco2    DECIMAL(10,6)   NULL COMMENT 'tCO₂/MWh fator emissão SIN do período',
    carbon_avoided_kg       DECIMAL(12,4)   NULL COMMENT 'kgCO₂ evitados',
    -- Merkle
    merkle_leaf_hash        VARCHAR(66)     NULL COMMENT 'SHA-256 da folha',
    merkle_proof_json       JSON            NULL COMMENT 'path de prova Merkle',
    -- Rastreabilidade blockchain
    on_chain_token_amount   DECIMAL(18,3)   NULL COMMENT 'qtd mintada (mHEC/HEC)',
    -- Status
    status                  ENUM('eligible','minted','transferred','claimed','retired','voided') NOT NULL DEFAULT 'eligible',
    minted_at               DATETIME        NULL,
    created_at              DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_cert_uuid (uuid),
    KEY idx_cert_batch (batch_id),
    KEY idx_cert_site (site_id),
    KEY idx_cert_interval (interval_start, interval_end),
    KEY idx_cert_status (status),
    KEY idx_cert_tier (tier),
    CONSTRAINT fk_cert_batch  FOREIGN KEY (batch_id)  REFERENCES certificate_batches(id),
    CONSTRAINT fk_cert_site   FOREIGN KEY (site_id)   REFERENCES sites(id),
    CONSTRAINT fk_cert_device FOREIGN KEY (device_id) REFERENCES devices(id)
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────────────────────────────────────
-- 9. OWNERSHIP / TRANSFERS
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE certificate_ownership (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    certificate_id  BIGINT UNSIGNED NOT NULL,
    wallet_id       BIGINT UNSIGNED NOT NULL,
    amount_wh       BIGINT          NOT NULL COMMENT 'fração em Wh detida',
    acquired_at     DATETIME        NOT NULL,
    released_at     DATETIME        NULL,
    status          ENUM('held','transferred','claimed','retired') NOT NULL DEFAULT 'held',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_own_cert   (certificate_id),
    KEY idx_own_wallet (wallet_id),
    CONSTRAINT fk_own_cert   FOREIGN KEY (certificate_id) REFERENCES certificates(id),
    CONSTRAINT fk_own_wallet FOREIGN KEY (wallet_id)      REFERENCES wallets(id)
) ENGINE=InnoDB;

CREATE TABLE transfers (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    uuid                CHAR(36)        NOT NULL,
    certificate_id      BIGINT UNSIGNED NOT NULL,
    from_wallet_id      BIGINT UNSIGNED NOT NULL,
    to_wallet_id        BIGINT UNSIGNED NOT NULL,
    amount_wh           BIGINT          NOT NULL,
    tx_hash             VARCHAR(66)     NULL,
    reason              ENUM('sale','gift','internal','marketplace','correction') NOT NULL DEFAULT 'sale',
    status              ENUM('pending','confirmed','failed','reversed') NOT NULL DEFAULT 'pending',
    confirmed_at        DATETIME        NULL,
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_transfer_uuid (uuid),
    KEY idx_transfer_cert   (certificate_id),
    KEY idx_transfer_from   (from_wallet_id),
    KEY idx_transfer_to     (to_wallet_id),
    KEY idx_transfer_status (status),
    CONSTRAINT fk_xfer_cert FOREIGN KEY (certificate_id) REFERENCES certificates(id),
    CONSTRAINT fk_xfer_from FOREIGN KEY (from_wallet_id) REFERENCES wallets(id),
    CONSTRAINT fk_xfer_to   FOREIGN KEY (to_wallet_id)   REFERENCES wallets(id)
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────────────────────────────────────
-- 10. CLAIMS / RETIREMENT / BURN
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE claims (
    id                      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    uuid                    CHAR(36)        NOT NULL,
    certificate_id          BIGINT UNSIGNED NOT NULL,
    wallet_id               BIGINT UNSIGNED NOT NULL COMMENT 'wallet que queimou',
    beneficiary_org_id      BIGINT UNSIGNED NULL,
    beneficiary_user_id     BIGINT UNSIGNED NULL,
    beneficiary_name        VARCHAR(255)    NULL COMMENT 'nome externo se beneficiário não é user',
    amount_wh               BIGINT          NOT NULL COMMENT 'energia reivindicada (Wh)',
    -- Finalidade
    purpose                 ENUM('scope2_market','scope2_location','scope3','voluntary','poce_streaming','poce_datacenter','poce_event','other') NOT NULL,
    scope                   VARCHAR(50)     NULL COMMENT 'GHG Protocol scope detail',
    reporting_period_start  DATE            NULL,
    reporting_period_end    DATE            NULL,
    region_h3               VARCHAR(20)     NULL,
    -- Prova
    report_hash             VARCHAR(66)     NULL COMMENT 'SHA-256 do relatório',
    report_uri              VARCHAR(500)    NULL COMMENT 'IPFS ou URL do relatório',
    signature               VARCHAR(200)    NULL COMMENT 'assinatura digital',
    -- Blockchain
    merkle_leaf_hash        VARCHAR(66)     NULL,
    merkle_proof_json       JSON            NULL,
    tx_hash_burn            VARCHAR(66)     NULL,
    -- Fator emissão no momento da claim
    emission_factor_tco2    DECIMAL(10,6)   NULL,
    carbon_offset_kg        DECIMAL(12,4)   NULL,
    status                  ENUM('pending','confirmed','revoked') NOT NULL DEFAULT 'pending',
    confirmed_at            DATETIME        NULL,
    created_at              DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_claim_uuid (uuid),
    KEY idx_claim_cert      (certificate_id),
    KEY idx_claim_wallet    (wallet_id),
    KEY idx_claim_benef_org (beneficiary_org_id),
    KEY idx_claim_purpose   (purpose),
    KEY idx_claim_period    (reporting_period_start, reporting_period_end),
    CONSTRAINT fk_claim_cert      FOREIGN KEY (certificate_id)     REFERENCES certificates(id),
    CONSTRAINT fk_claim_wallet    FOREIGN KEY (wallet_id)          REFERENCES wallets(id),
    CONSTRAINT fk_claim_benef_org FOREIGN KEY (beneficiary_org_id) REFERENCES organizations(id),
    CONSTRAINT fk_claim_benef_usr FOREIGN KEY (beneficiary_user_id) REFERENCES users(id)
) ENGINE=InnoDB;

CREATE TABLE retirements (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    uuid            CHAR(36)        NOT NULL,
    claim_id        BIGINT UNSIGNED NOT NULL,
    batch_id        BIGINT UNSIGNED NULL,
    retired_amount_wh BIGINT        NOT NULL,
    retirement_reason VARCHAR(500)  NULL,
    tx_hash_burn    VARCHAR(66)     NULL,
    ipfs_cid_proof  VARCHAR(100)    NULL,
    retired_at      DATETIME        NOT NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_retire_uuid (uuid),
    KEY idx_retire_claim (claim_id),
    CONSTRAINT fk_retire_claim FOREIGN KEY (claim_id) REFERENCES claims(id),
    CONSTRAINT fk_retire_batch FOREIGN KEY (batch_id) REFERENCES certificate_batches(id)
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────────────────────────────────────
-- 11. dNFT EVOLUÇÃO & PLACA FREE CARBON
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE dnft_progress (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id             BIGINT UNSIGNED NOT NULL,
    wallet_id           BIGINT UNSIGNED NOT NULL,
    current_degree      ENUM('semente','broto','arvore','floresta','vida') NOT NULL DEFAULT 'semente',
    total_mhec_burned   BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'acumulado de mHECs queimados',
    token_id_erc        INT UNSIGNED    NOT NULL DEFAULT 100 COMMENT '#100-104',
    tx_hash_last_evolve VARCHAR(66)     NULL,
    evolved_at          DATETIME        NULL,
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_dnft_user (user_id),
    CONSTRAINT fk_dnft_user   FOREIGN KEY (user_id)   REFERENCES users(id),
    CONSTRAINT fk_dnft_wallet FOREIGN KEY (wallet_id) REFERENCES wallets(id)
) ENGINE=InnoDB;

CREATE TABLE placa_free_carbon (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id             BIGINT UNSIGNED NOT NULL,
    subscription_id     BIGINT UNSIGNED NULL,
    current_level       ENUM('none','bronze','silver','gold','diamond') NOT NULL DEFAULT 'none',
    streak_months       INT UNSIGNED    NOT NULL DEFAULT 0,
    streak_min_pct      DECIMAL(5,2)    NOT NULL DEFAULT 95.00 COMMENT 'limiar ≥95%',
    streak_current_pct  DECIMAL(5,2)    NULL,
    token_id_erc        INT UNSIGNED    NULL COMMENT '#200-203',
    tx_hash_award       VARCHAR(66)     NULL,
    awarded_at          DATETIME        NULL,
    physical_shipped    TINYINT(1)      NOT NULL DEFAULT 0,
    shipping_address    VARCHAR(500)    NULL,
    tracking_code       VARCHAR(100)    NULL,
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_placa_user (user_id),
    CONSTRAINT fk_placa_user FOREIGN KEY (user_id) REFERENCES users(id),
    CONSTRAINT fk_placa_sub  FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────────────────────────────────────
-- 12. MARKETPLACE
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE marketplace_listings (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    uuid            CHAR(36)        NOT NULL,
    seller_wallet_id BIGINT UNSIGNED NOT NULL,
    certificate_id  BIGINT UNSIGNED NOT NULL,
    amount_wh       BIGINT          NOT NULL,
    price_per_mwh   DECIMAL(10,2)   NOT NULL COMMENT 'preço unitário BRL / MWh',
    tier            ENUM('standard','granular','premium') NOT NULL,
    ouro_verde_mult DECIMAL(4,2)    NOT NULL DEFAULT 1.00 COMMENT 'multiplicador Ouro Verde 0.5-3.0',
    expires_at      DATETIME        NULL,
    status          ENUM('active','sold','cancelled','expired') NOT NULL DEFAULT 'active',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_listing_uuid (uuid),
    KEY idx_listing_seller (seller_wallet_id),
    KEY idx_listing_cert (certificate_id),
    KEY idx_listing_status (status, tier),
    CONSTRAINT fk_listing_seller FOREIGN KEY (seller_wallet_id) REFERENCES wallets(id),
    CONSTRAINT fk_listing_cert   FOREIGN KEY (certificate_id)   REFERENCES certificates(id)
) ENGINE=InnoDB;

CREATE TABLE marketplace_orders (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    uuid            CHAR(36)        NOT NULL,
    listing_id      BIGINT UNSIGNED NOT NULL,
    buyer_wallet_id BIGINT UNSIGNED NOT NULL,
    amount_wh       BIGINT          NOT NULL,
    total_brl       DECIMAL(14,2)   NOT NULL,
    fee_brl         DECIMAL(10,2)   NOT NULL DEFAULT 0 COMMENT 'comissão plataforma',
    status          ENUM('pending','paid','completed','cancelled','refunded') NOT NULL DEFAULT 'pending',
    paid_at         DATETIME        NULL,
    completed_at    DATETIME        NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_order_uuid (uuid),
    KEY idx_order_listing (listing_id),
    KEY idx_order_buyer (buyer_wallet_id),
    KEY idx_order_status (status),
    CONSTRAINT fk_order_listing FOREIGN KEY (listing_id)      REFERENCES marketplace_listings(id),
    CONSTRAINT fk_order_buyer   FOREIGN KEY (buyer_wallet_id) REFERENCES wallets(id)
) ENGINE=InnoDB;

CREATE TABLE marketplace_trades (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    order_id        BIGINT UNSIGNED NOT NULL,
    transfer_id     BIGINT UNSIGNED NOT NULL,
    trade_price_brl DECIMAL(14,2)   NOT NULL,
    fee_brl         DECIMAL(10,2)   NOT NULL DEFAULT 0,
    settled_at      DATETIME        NOT NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_trade_order (order_id),
    CONSTRAINT fk_trade_order    FOREIGN KEY (order_id)    REFERENCES marketplace_orders(id),
    CONSTRAINT fk_trade_transfer FOREIGN KEY (transfer_id) REFERENCES transfers(id)
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────────────────────────────────────
-- 13. PAGAMENTOS / INVOICES
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE payments (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    uuid                CHAR(36)        NOT NULL,
    user_id             BIGINT UNSIGNED NOT NULL,
    org_id              BIGINT UNSIGNED NULL,
    wallet_id           BIGINT UNSIGNED NULL,
    payment_method      ENUM('pix','credit_card','boleto','wire','auto_recharge') NOT NULL,
    gateway             VARCHAR(50)     NULL COMMENT 'stripe, pagarme, etc.',
    gateway_tx_id       VARCHAR(200)    NULL,
    amount_brl          DECIMAL(14,2)   NOT NULL,
    fee_brl             DECIMAL(10,2)   NOT NULL DEFAULT 0,
    currency            CHAR(3)         NOT NULL DEFAULT 'BRL',
    description         VARCHAR(500)    NULL,
    status              ENUM('pending','processing','confirmed','failed','refunded') NOT NULL DEFAULT 'pending',
    confirmed_at        DATETIME        NULL,
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_payment_uuid (uuid),
    KEY idx_payment_user   (user_id),
    KEY idx_payment_org    (org_id),
    KEY idx_payment_status (status),
    CONSTRAINT fk_payment_user   FOREIGN KEY (user_id)   REFERENCES users(id),
    CONSTRAINT fk_payment_org    FOREIGN KEY (org_id)    REFERENCES organizations(id),
    CONSTRAINT fk_payment_wallet FOREIGN KEY (wallet_id) REFERENCES wallets(id)
) ENGINE=InnoDB;

CREATE TABLE invoices (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    uuid            CHAR(36)        NOT NULL,
    org_id          BIGINT UNSIGNED NULL,
    user_id         BIGINT UNSIGNED NULL,
    contract_id     BIGINT UNSIGNED NULL,
    invoice_number  VARCHAR(50)     NOT NULL,
    period_start    DATE            NOT NULL,
    period_end      DATE            NOT NULL,
    subtotal_brl    DECIMAL(14,2)   NOT NULL,
    tax_brl         DECIMAL(10,2)   NOT NULL DEFAULT 0,
    total_brl       DECIMAL(14,2)   NOT NULL,
    currency        CHAR(3)         NOT NULL DEFAULT 'BRL',
    status          ENUM('draft','issued','paid','overdue','cancelled') NOT NULL DEFAULT 'draft',
    due_date        DATE            NOT NULL,
    paid_at         DATETIME        NULL,
    pdf_uri         VARCHAR(500)    NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_invoice_uuid (uuid),
    UNIQUE KEY uq_invoice_number (invoice_number),
    KEY idx_invoice_org  (org_id),
    KEY idx_invoice_user (user_id),
    CONSTRAINT fk_invoice_org      FOREIGN KEY (org_id)      REFERENCES organizations(id),
    CONSTRAINT fk_invoice_user     FOREIGN KEY (user_id)     REFERENCES users(id),
    CONSTRAINT fk_invoice_contract FOREIGN KEY (contract_id) REFERENCES contracts(id)
) ENGINE=InnoDB;

CREATE TABLE invoice_lines (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    invoice_id      BIGINT UNSIGNED NOT NULL,
    description     VARCHAR(500)    NOT NULL,
    quantity        DECIMAL(14,3)   NOT NULL,
    unit            VARCHAR(20)     NOT NULL DEFAULT 'MWh',
    unit_price_brl  DECIMAL(10,4)   NOT NULL,
    total_brl       DECIMAL(14,2)   NOT NULL,
    certificate_id  BIGINT UNSIGNED NULL,
    claim_id        BIGINT UNSIGNED NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_invline_invoice (invoice_id),
    CONSTRAINT fk_invline_invoice FOREIGN KEY (invoice_id)     REFERENCES invoices(id) ON DELETE CASCADE,
    CONSTRAINT fk_invline_cert    FOREIGN KEY (certificate_id) REFERENCES certificates(id),
    CONSTRAINT fk_invline_claim   FOREIGN KEY (claim_id)       REFERENCES claims(id)
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────────────────────────────────────
-- 14. MERKLE TREES (off-chain rastreabilidade)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE merkle_trees (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    tree_date       DATE            NOT NULL,
    tree_type       ENUM('mint','burn') NOT NULL,
    root_hash       VARCHAR(66)     NOT NULL,
    leaf_count      INT UNSIGNED    NOT NULL,
    ipfs_cid        VARCHAR(100)    NULL,
    tx_hash         VARCHAR(66)     NULL COMMENT 'commitMerkleRoot() on-chain',
    block_number    BIGINT UNSIGNED NULL,
    network         VARCHAR(30)     NOT NULL DEFAULT 'polygon',
    status          ENUM('building','committed','verified','failed') NOT NULL DEFAULT 'building',
    committed_at    DATETIME        NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_merkle_date_type (tree_date, tree_type),
    KEY idx_merkle_root (root_hash)
) ENGINE=InnoDB;

CREATE TABLE merkle_leaves (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    tree_id         BIGINT UNSIGNED NOT NULL,
    leaf_index      INT UNSIGNED    NOT NULL,
    leaf_hash       VARCHAR(66)     NOT NULL,
    entity_type     ENUM('certificate','claim','poce_session') NOT NULL,
    entity_id       BIGINT UNSIGNED NOT NULL COMMENT 'certificates.id ou claims.id',
    data_hash       VARCHAR(66)     NOT NULL COMMENT 'SHA-256 dos dados originais',
    proof_json      JSON            NULL COMMENT 'merkle proof path',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_mleaf_tree (tree_id),
    KEY idx_mleaf_entity (entity_type, entity_id),
    CONSTRAINT fk_mleaf_tree FOREIGN KEY (tree_id) REFERENCES merkle_trees(id)
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────────────────────────────────────
-- 15. CHAINLINK / ORACLES
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE oracle_feeds (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    feed_date           DATE            NOT NULL,
    oracle_provider     VARCHAR(50)     NOT NULL DEFAULT 'chainlink',
    feed_type           ENUM('emission_factor_sin','pol_usd','custom') NOT NULL,
    value_numeric       DECIMAL(18,8)   NOT NULL,
    value_unit          VARCHAR(30)     NOT NULL COMMENT 'e.g. tCO2/MWh, USD',
    source_description  VARCHAR(300)    NULL,
    tx_hash             VARCHAR(66)     NULL,
    block_number        BIGINT UNSIGNED NULL,
    fetched_at          DATETIME        NOT NULL,
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_oracle_feed (feed_date, feed_type),
    KEY idx_oracle_date (feed_date)
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────────────────────────────────────
-- 16. DS / VALIDAÇÃO — PIPELINE RUNS, MODELOS, SCORES
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE ds_pipeline_runs (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    uuid                CHAR(36)        NOT NULL,
    run_type            ENUM('qsv_hourly','qsv_daily','anomaly_detection','curtailment','recalculation','poce_emission') NOT NULL,
    model_name          VARCHAR(100)    NOT NULL COMMENT 'e.g. xgboost_qsv_v3, lstm_anomaly_v2',
    model_version       VARCHAR(50)     NOT NULL,
    h3_region_id        BIGINT UNSIGNED NULL,
    site_id             BIGINT UNSIGNED NULL,
    -- Janela de dados processados
    data_start          DATETIME        NOT NULL,
    data_end            DATETIME        NOT NULL,
    -- Resultado
    records_processed   INT UNSIGNED    NOT NULL DEFAULT 0,
    records_eligible    INT UNSIGNED    NOT NULL DEFAULT 0,
    records_flagged     INT UNSIGNED    NOT NULL DEFAULT 0,
    -- Metadados
    features_json       JSON            NULL COMMENT 'lista de features usadas',
    hyperparams_json    JSON            NULL,
    metrics_json        JSON            NULL COMMENT 'accuracy, f1, rmse etc.',
    duration_ms         INT UNSIGNED    NULL,
    status              ENUM('queued','running','completed','failed') NOT NULL DEFAULT 'queued',
    error_message       TEXT            NULL,
    started_at          DATETIME        NULL,
    completed_at        DATETIME        NULL,
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_dsrun_uuid (uuid),
    KEY idx_dsrun_type   (run_type),
    KEY idx_dsrun_region (h3_region_id),
    KEY idx_dsrun_site   (site_id),
    KEY idx_dsrun_window (data_start, data_end),
    CONSTRAINT fk_dsrun_region FOREIGN KEY (h3_region_id) REFERENCES h3_regions(id),
    CONSTRAINT fk_dsrun_site   FOREIGN KEY (site_id)      REFERENCES sites(id)
) ENGINE=InnoDB;

CREATE TABLE ds_validation_results (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id          BIGINT UNSIGNED NOT NULL,
    certificate_id  BIGINT UNSIGNED NULL,
    site_id         BIGINT UNSIGNED NOT NULL,
    interval_start  DATETIME        NOT NULL,
    interval_end    DATETIME        NOT NULL,
    -- Scores por fonte
    s1_inverter_ok  TINYINT(1)      NULL,
    s2_satellite_ok TINYINT(1)      NULL,
    s3_weather_ok   TINYINT(1)      NULL,
    s4_neighbors_ok TINYINT(1)      NULL,
    qsv_score       TINYINT UNSIGNED NOT NULL,
    p_truth         DECIMAL(5,4)    NOT NULL,
    -- Decisão
    decision        ENUM('eligible','not_eligible','needs_review','voided') NOT NULL,
    flags_json      JSON            NULL COMMENT 'anomalias detectadas',
    notes           VARCHAR(1000)   NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_dsval_run      (run_id),
    KEY idx_dsval_cert     (certificate_id),
    KEY idx_dsval_site     (site_id),
    KEY idx_dsval_interval (interval_start, interval_end),
    CONSTRAINT fk_dsval_run  FOREIGN KEY (run_id)         REFERENCES ds_pipeline_runs(id),
    CONSTRAINT fk_dsval_cert FOREIGN KEY (certificate_id) REFERENCES certificates(id),
    CONSTRAINT fk_dsval_site FOREIGN KEY (site_id)        REFERENCES sites(id)
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────────────────────────────────────
-- 17. PoCE — SESSÕES DE STREAMING / DATACENTER
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE poce_sessions (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    uuid                CHAR(36)        NOT NULL,
    user_id             BIGINT UNSIGNED NOT NULL,
    subscription_id     BIGINT UNSIGNED NULL,
    platform            ENUM('youtube','twitch','tiktok','custom_webapp','datacenter','event') NOT NULL,
    session_start       DATETIME        NOT NULL,
    session_end         DATETIME        NULL,
    duration_seconds    INT UNSIGNED    NULL,
    -- Telemetria
    video_resolution    VARCHAR(10)     NULL COMMENT '4K, 1080p, 720p, 480p',
    cdn_location        VARCHAR(100)    NULL,
    datacenter          VARCHAR(100)    NULL,
    peak_viewers        INT UNSIGNED    NULL,
    avg_bitrate_kbps    INT UNSIGNED    NULL,
    -- Cálculo PoCE
    energy_consumed_wh  BIGINT          NOT NULL DEFAULT 0 COMMENT 'kWh server-side calculado',
    emission_factor     DECIMAL(10,6)   NULL COMMENT 'tCO₂/MWh no momento',
    mhec_required       DECIMAL(14,3)   NOT NULL DEFAULT 0,
    mhec_burned         DECIMAL(14,3)   NOT NULL DEFAULT 0,
    -- Resultado
    matching_pct        DECIMAL(5,2)    NULL COMMENT '% matching horário nesta sessão',
    poce_scope          ENUM('server_only','edge_to_edge','full_chain') NOT NULL DEFAULT 'server_only',
    merkle_leaf_hash    VARCHAR(66)     NULL,
    status              ENUM('active','completed','partial','failed') NOT NULL DEFAULT 'active',
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_poce_uuid (uuid),
    KEY idx_poce_user (user_id),
    KEY idx_poce_platform (platform),
    KEY idx_poce_session (session_start, session_end),
    CONSTRAINT fk_poce_user FOREIGN KEY (user_id)          REFERENCES users(id),
    CONSTRAINT fk_poce_sub  FOREIGN KEY (subscription_id)  REFERENCES subscriptions(id)
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────────────────────────────────────
-- 18. iCLEANSOLAR — MARKETPLACE MANUTENÇÃO
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE icleansolar_technicians (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id         BIGINT UNSIGNED NOT NULL,
    service_area_h3 VARCHAR(20)     NULL,
    latitude        DECIMAL(10,7)   NULL,
    longitude       DECIMAL(10,7)   NULL,
    rating          DECIMAL(3,2)    NOT NULL DEFAULT 0 COMMENT '0-5',
    total_jobs      INT UNSIGNED    NOT NULL DEFAULT 0,
    certified       TINYINT(1)      NOT NULL DEFAULT 0,
    status          ENUM('available','busy','offline','suspended') NOT NULL DEFAULT 'offline',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_tech_user (user_id),
    CONSTRAINT fk_tech_user FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB;

CREATE TABLE icleansolar_service_requests (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    uuid            CHAR(36)        NOT NULL,
    site_id         BIGINT UNSIGNED NOT NULL,
    requester_id    BIGINT UNSIGNED NOT NULL,
    technician_id   BIGINT UNSIGNED NULL,
    alert_type      ENUM('dirt_detected','performance_drop','hardware_fault','scheduled','manual') NOT NULL,
    severity        ENUM('low','medium','high','critical') NOT NULL DEFAULT 'medium',
    description     VARCHAR(1000)   NULL,
    estimated_brl   DECIMAL(10,2)   NULL,
    final_brl       DECIMAL(10,2)   NULL,
    commission_brl  DECIMAL(10,2)   NULL,
    scheduled_at    DATETIME        NULL,
    completed_at    DATETIME        NULL,
    rating          TINYINT UNSIGNED NULL COMMENT '1-5',
    status          ENUM('open','assigned','in_progress','completed','cancelled') NOT NULL DEFAULT 'open',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_svcreq_uuid (uuid),
    KEY idx_svcreq_site (site_id),
    KEY idx_svcreq_tech (technician_id),
    KEY idx_svcreq_status (status),
    CONSTRAINT fk_svcreq_site FOREIGN KEY (site_id)       REFERENCES sites(id),
    CONSTRAINT fk_svcreq_req  FOREIGN KEY (requester_id)  REFERENCES users(id),
    CONSTRAINT fk_svcreq_tech FOREIGN KEY (technician_id) REFERENCES icleansolar_technicians(id)
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────────────────────────────────────
-- 19. INTEGRAÇÕES EXTERNAS
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE integrations (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    org_id          BIGINT UNSIGNED NULL,
    provider        VARCHAR(100)    NOT NULL COMMENT 'e.g. youtube, twitch, tiktok, aws, copernicus, inmet',
    integration_type ENUM('inverter_api','streaming_api','cloud_provider','weather_api','satellite_api','payment_gw','webhook') NOT NULL,
    credentials_enc TEXT            NULL COMMENT 'AES-256-GCM encrypted',
    endpoint_url    VARCHAR(500)    NULL,
    is_active       TINYINT(1)      NOT NULL DEFAULT 1,
    last_sync_at    DATETIME        NULL,
    error_count     INT UNSIGNED    NOT NULL DEFAULT 0,
    metadata_json   JSON            NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_integ_org (org_id),
    KEY idx_integ_provider (provider),
    CONSTRAINT fk_integ_org FOREIGN KEY (org_id) REFERENCES organizations(id)
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────────────────────────────────────
-- 20. JOBS / FILAS ASSÍNCRONAS
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE jobs (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    uuid            CHAR(36)        NOT NULL,
    job_type        VARCHAR(100)    NOT NULL COMMENT 'e.g. daily_mint_batch, daily_burn_batch, merkle_commit, qsv_run',
    priority        TINYINT UNSIGNED NOT NULL DEFAULT 5 COMMENT '1=highest, 10=lowest',
    payload_json    JSON            NOT NULL,
    scheduled_for   DATETIME        NULL,
    attempts        TINYINT UNSIGNED NOT NULL DEFAULT 0,
    max_attempts    TINYINT UNSIGNED NOT NULL DEFAULT 3,
    last_error      TEXT            NULL,
    status          ENUM('queued','running','completed','failed','cancelled') NOT NULL DEFAULT 'queued',
    started_at      DATETIME        NULL,
    completed_at    DATETIME        NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_job_uuid (uuid),
    KEY idx_job_type_status (job_type, status),
    KEY idx_job_scheduled (scheduled_for),
    KEY idx_job_status (status)
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────────────────────────────────────
-- 21. AUDITORIA (append-only)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE audit_log (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    event_type      VARCHAR(80)     NOT NULL COMMENT 'mint, burn, transfer, claim, retire, qsv_run, anomaly, adjustment, login, etc.',
    severity        ENUM('info','warning','error','critical') NOT NULL DEFAULT 'info',
    actor_type      ENUM('user','system','oracle','pipeline','admin') NOT NULL,
    actor_id        BIGINT UNSIGNED NULL COMMENT 'user.id se aplicável',
    org_id          BIGINT UNSIGNED NULL,
    entity_type     VARCHAR(80)     NULL COMMENT 'certificate, claim, batch, site, etc.',
    entity_id       BIGINT UNSIGNED NULL,
    description     VARCHAR(2000)   NOT NULL,
    old_value_json  JSON            NULL,
    new_value_json  JSON            NULL,
    ip_address      VARCHAR(45)     NULL,
    user_agent      VARCHAR(500)    NULL,
    tx_hash         VARCHAR(66)     NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_audit_event     (event_type),
    KEY idx_audit_actor     (actor_type, actor_id),
    KEY idx_audit_entity    (entity_type, entity_id),
    KEY idx_audit_org       (org_id),
    KEY idx_audit_created   (created_at),
    KEY idx_audit_severity  (severity, created_at)
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────────────────────────────────────
-- 22. OURO VERDE — PRICING DINÂMICO
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE ouro_verde_pricing (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    effective_from  DATETIME        NOT NULL,
    effective_to    DATETIME        NULL,
    scenario        ENUM('discount_solar','base','peak') NOT NULL,
    multiplier      DECIMAL(4,2)    NOT NULL COMMENT '0.5x, 1x, 3x',
    aneel_flag      ENUM('green','yellow','red') NULL COMMENT 'bandeira tarifária',
    region_h3       VARCHAR(20)     NULL COMMENT 'NULL = nacional',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_ouro_period (effective_from, effective_to),
    KEY idx_ouro_scenario (scenario)
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────────────────────────────────────
-- 23. NOTIFICAÇÕES / ALERTAS
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE notifications (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id         BIGINT UNSIGNED NOT NULL,
    channel         ENUM('push','email','sms','in_app') NOT NULL DEFAULT 'in_app',
    category        VARCHAR(80)     NOT NULL COMMENT 'streak_warning, placa_awarded, anomaly, icleansolar_alert, payment',
    title           VARCHAR(255)    NOT NULL,
    body            TEXT            NOT NULL,
    is_read         TINYINT(1)      NOT NULL DEFAULT 0,
    action_url      VARCHAR(500)    NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    read_at         DATETIME        NULL,
    PRIMARY KEY (id),
    KEY idx_notif_user (user_id, is_read),
    KEY idx_notif_category (category),
    CONSTRAINT fk_notif_user FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────────────────────────────────────
-- 24. EMISSION FACTORS HISTÓRICOS (cache local do SIN/ONS)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE emission_factors (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    reference_date      DATE            NOT NULL,
    granularity         ENUM('hourly','daily','monthly','annual') NOT NULL DEFAULT 'monthly',
    hour_utc            TINYINT UNSIGNED NULL COMMENT '0-23 se hourly',
    source              VARCHAR(100)    NOT NULL COMMENT 'MCTI/SIRENE, ONS, Chainlink',
    factor_tco2_per_mwh DECIMAL(10,6)   NOT NULL,
    grid_region         VARCHAR(30)     NOT NULL DEFAULT 'SIN' COMMENT 'SIN, SE/CO, S, NE, N',
    metadata_json       JSON            NULL,
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_emfactor (reference_date, granularity, hour_utc, grid_region),
    KEY idx_emfactor_date (reference_date)
) ENGINE=InnoDB;

-- ─────────────────────────────────────────────────────────────────────────────
-- 25. CURTAILMENT (registros de curtailment detectados)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE curtailment_events (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    site_id         BIGINT UNSIGNED NOT NULL,
    detected_by     ENUM('qsv','ml_pipeline','manual') NOT NULL DEFAULT 'qsv',
    ds_run_id       BIGINT UNSIGNED NULL,
    event_start     DATETIME        NOT NULL,
    event_end       DATETIME        NULL,
    expected_wh     BIGINT          NOT NULL COMMENT 'energia esperada (Wh)',
    actual_wh       BIGINT          NOT NULL COMMENT 'energia real (Wh)',
    curtailed_wh    BIGINT          NOT NULL COMMENT 'diferença (Wh)',
    cause           ENUM('grid_limit','inverter_limit','manual_shutdown','unknown') NULL,
    merkle_leaf_hash VARCHAR(66)    NULL,
    status          ENUM('detected','confirmed','disputed','resolved') NOT NULL DEFAULT 'detected',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_curtail_site  (site_id),
    KEY idx_curtail_event (event_start, event_end),
    CONSTRAINT fk_curtail_site FOREIGN KEY (site_id)   REFERENCES sites(id),
    CONSTRAINT fk_curtail_run  FOREIGN KEY (ds_run_id) REFERENCES ds_pipeline_runs(id)
) ENGINE=InnoDB;

SET FOREIGN_KEY_CHECKS = 1;

-- =============================================================================
-- FIM DO SCHEMA MYSQL
-- =============================================================================

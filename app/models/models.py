import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Float, Integer, DateTime, ForeignKey,
    Text, Numeric, JSON, Boolean, Enum as SAEnum, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.db.session import Base


# ---------------------------------------------------------------------------
# PLANTS — Usinas e sistemas de geração solar
# ---------------------------------------------------------------------------
class Plant(Base):
    __tablename__ = "plants"

    plant_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    absolar_id = Column(String(100), unique=True, nullable=True, comment="ID ABSOLAR associado")
    owner_name = Column(String(255), nullable=True)
    owner_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id"),
        nullable=True,
        index=True,
        comment="Usuario dono da usina (onboarding gerador)",
    )
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    capacity_kw = Column(Numeric(12, 3), nullable=False, comment="Potência instalada kWp")
    status = Column(
        String(20), nullable=False, default="active",
        comment="active | inactive | maintenance | pending",
    )
    inverter_brand = Column(String(100), nullable=True)
    inverter_model = Column(String(100), nullable=True)
    commissioning_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    telemetry = relationship("Telemetry", back_populates="plant", cascade="all, delete-orphan")
    validations = relationship("Validation", back_populates="plant", cascade="all, delete-orphan")
    owner = relationship("User", back_populates="plants")
    generator_connections = relationship("GeneratorInverterConnection", back_populates="plant")


# ---------------------------------------------------------------------------
# TELEMETRY — Hypertable: séries temporais dos inversores
# ---------------------------------------------------------------------------
class Telemetry(Base):
    __tablename__ = "telemetry"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    time = Column(DateTime, nullable=False, index=True, comment="Timestamp da leitura")
    plant_id = Column(UUID(as_uuid=True), ForeignKey("plants.plant_id"), nullable=False, index=True)
    power_kw = Column(Numeric(12, 4), nullable=False, comment="Potência instantânea kW")
    energy_kwh = Column(Numeric(14, 4), nullable=False, comment="Energia acumulada kWh")
    voltage_v = Column(Numeric(8, 2), nullable=True)
    temperature_c = Column(Numeric(6, 2), nullable=True)
    irradiance_wm2 = Column(Numeric(8, 2), nullable=True, comment="Irradiância W/m²")
    source = Column(String(50), nullable=False, default="mqtt", comment="mqtt | api | manual")
    pre_commitment_hash = Column(String(256), nullable=True, comment="ECDSA pre-commitment hash")
    ntp_delta_ms = Column(Float, nullable=True, comment="Delta NTP em ms")
    ntp_pass = Column(Boolean, nullable=True, comment="True se |drift| <= 5ms (Camada 2 NTP Blindada)")
    raw_payload = Column(JSONB, nullable=True, comment="Payload bruto do inversor")
    payload_sha256 = Column(String(64), nullable=True, comment="SHA-256 do payload recebido")
    nonce = Column(String(64), nullable=True, index=True, comment="Nonce anti-replay")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    plant = relationship("Plant", back_populates="telemetry")


# ---------------------------------------------------------------------------
# USED_NONCES — Anti-replay: nonces já utilizados (TTL 60s)
# ---------------------------------------------------------------------------
class UsedNonce(Base):
    __tablename__ = "used_nonces"

    nonce = Column(String(64), primary_key=True)
    plant_id = Column(UUID(as_uuid=True), nullable=False)
    used_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


# ---------------------------------------------------------------------------
# VALIDATIONS — Resultado validação SOS/SENTINEL por período
# ---------------------------------------------------------------------------
class Validation(Base):
    __tablename__ = "validations"

    validation_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plant_id = Column(UUID(as_uuid=True), ForeignKey("plants.plant_id"), nullable=False, index=True)
    telemetry_id = Column(UUID(as_uuid=True), nullable=True, comment="ID da telemetria que originou esta validação")
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    energy_kwh = Column(Numeric(14, 4), nullable=False, comment="Total energia validada no período")
    confidence_score = Column(Numeric(5, 2), nullable=False, comment="Score 0-100 SENTINEL AGIS")
    anomaly_flags = Column(JSONB, nullable=True, comment="Flags de anomalia detectadas")
    status = Column(
        String(20), nullable=False, default="pending",
        comment="pending | approved | rejected | review",
    )

    # ── Camada 2: NTP Blindada ────────────────────────────────────
    ntp_pass = Column(Boolean, nullable=True, comment="True se |drift NTP| <= 5ms")
    ntp_drift_ms = Column(Float, nullable=True, comment="Drift NTP medido (ms)")

    # ── Camada 3: Física Teórica (Patente 33 / pvlib) ────────────
    theoretical_max_kwh = Column(Numeric(14, 4), nullable=True,
                                  comment="Geração máxima teórica no intervalo (kWh)")
    theoretical_max_kw = Column(Numeric(12, 4), nullable=True,
                                 comment="Potência máxima teórica instantânea (kW)")
    ghi_clear_sky_wm2 = Column(Numeric(8, 2), nullable=True,
                                comment="Irradiância clear-sky estimada (W/m²)")
    solar_elevation_deg = Column(Numeric(6, 2), nullable=True,
                                  comment="Elevação solar (graus)")
    physics_pass = Column(Boolean, nullable=True,
                          comment="True se energy_kwh <= theoretical_max_kwh")
    physics_method = Column(String(20), nullable=True,
                            comment="pvlib | analytical")

    # ── Camada 4: Validação Satélite (INPE GOES-16 / CAMS) ───────
    satellite_ghi_wm2 = Column(Numeric(8, 2), nullable=True,
                                comment="Irradiância GHI medida por satélite (W/m²)")
    satellite_source = Column(String(30), nullable=True,
                               comment="mock | inpe_goes16 | cams_copernicus")
    satellite_max_kwh = Column(Numeric(14, 4), nullable=True,
                                comment="Geração máx baseada em irradiância satélite (kWh)")
    satellite_pass = Column(Boolean, nullable=True,
                             comment="True se energy_kwh <= satellite_max_kwh")
    cloud_cover_pct = Column(Numeric(5, 1), nullable=True,
                              comment="Cobertura de nuvens estimada (%)")
    satellite_flags = Column(JSONB, nullable=True,
                              comment="Flags: low_irradiance, high_generation_low_sun")

    # ── Camada 5: Consenso Granular Geoespacial ──────────────────
    consensus_pass = Column(Boolean, nullable=True,
                             comment="True=ok, False=divergente, None=inconclusivo")
    consensus_deviation_pct = Column(Numeric(6, 2), nullable=True,
                                      comment="Desvio percentual da mediana vizinhas")
    consensus_median_ratio = Column(Numeric(10, 6), nullable=True,
                                     comment="Mediana kWh/kWp das vizinhas")
    consensus_plant_ratio = Column(Numeric(10, 6), nullable=True,
                                    comment="Ratio kWh/kWp da planta alvo")
    consensus_neighbors = Column(Integer, nullable=True,
                                  comment="Qtde vizinhas usadas no consenso")
    consensus_radius_km = Column(Numeric(6, 2), nullable=True,
                                  comment="Raio de busca usado (km)")
    consensus_details = Column(JSONB, nullable=True,
                                comment="Detalhes: vizinhas, distâncias, ratios")

    sentinel_version = Column(String(20), nullable=True, comment="Versão do SENTINEL AGIS")
    validation_details = Column(JSONB, nullable=True, comment="Detalhes extras da validação")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    plant = relationship("Plant", back_populates="validations")
    hec_certificate = relationship("HECCertificate", back_populates="validation", uselist=False)


# ---------------------------------------------------------------------------
# HEC_CERTIFICATES — Ativo digital: certificado de energia (NFT)
# ---------------------------------------------------------------------------
class HECCertificate(Base):
    __tablename__ = "hec_certificates"

    hec_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    validation_id = Column(
        UUID(as_uuid=True), ForeignKey("validations.validation_id"),
        unique=True, nullable=False,
    )
    lot_id = Column(UUID(as_uuid=True), ForeignKey("hec_lots.lot_id"), nullable=True)
    hash_sha256 = Column(String(64), unique=True, nullable=False,
                          comment="SHA-256 do JSON canônico do certificado")
    energy_kwh = Column(Numeric(14, 4), nullable=False)
    certificate_json = Column(JSONB, nullable=True,
                               comment="JSON canônico completo do certificado HEC")
    token_id = Column(String(100), nullable=True, comment="Token ID on-chain")
    contract_address = Column(String(42), nullable=True, comment="Endereço smart contract")
    chain = Column(String(20), nullable=True, default="polygon", comment="polygon | arbitrum")
    ipfs_cid = Column(String(100), nullable=True, comment="CID dos metadados IPFS (legacy)")
    ipfs_json_cid = Column(String(100), nullable=True,
                            comment="CID do JSON canônico no IPFS")
    ipfs_pdf_cid = Column(String(100), nullable=True,
                           comment="CID do PDF no IPFS")
    ipfs_provider = Column(String(20), nullable=True,
                            comment="Provider IPFS usado: mock | pinata | local")
    # On-chain registry
    registry_tx_hash = Column(String(66), nullable=True,
                               comment="Transaction hash do registro on-chain (0x...)")
    registry_block = Column(Integer, nullable=True,
                             comment="Block number do registro on-chain")
    registered_at = Column(DateTime, nullable=True,
                            comment="Timestamp do registro on-chain")
    status = Column(
        String(20), nullable=False, default="pending",
        comment="pending | registered | minted | listed | sold | retired",
    )
    minted_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    validation = relationship("Validation", back_populates="hec_certificate")
    lot = relationship("HECLot", back_populates="certificates")


# ---------------------------------------------------------------------------
# HEC_LOTS — Agrupamento de HECs para comercialização em lote
# ---------------------------------------------------------------------------
class HECLot(Base):
    __tablename__ = "hec_lots"

    lot_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    total_energy_kwh = Column(Numeric(16, 4), nullable=False, default=0,
                               comment="Soma total kWh de todos os HECs do lote")
    total_quantity = Column(Integer, nullable=False, default=0,
                             comment="Quantidade total de HECs no lote")
    available_quantity = Column(Integer, nullable=False, default=0,
                                 comment="Quantidade disponível (não vendidos/retirados)")
    certificate_count = Column(Integer, nullable=False, default=0,
                                comment="Alias legacy — same as total_quantity")
    price_per_kwh = Column(Numeric(10, 4), nullable=True,
                            comment="Preço por kWh em BRL (se listado)")
    status = Column(
        String(20), nullable=False, default="open",
        comment="open | closed | listed | sold",
    )
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    certificates = relationship("HECCertificate", back_populates="lot")
    transactions = relationship("Transaction", back_populates="lot")


# ---------------------------------------------------------------------------
# USERS — Usuários do marketplace
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    user_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=False,
                            comment="bcrypt hash da senha")
    role = Column(String(20), nullable=False, default="buyer",
                  comment="buyer | seller | admin")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    wallet = relationship("Wallet", back_populates="user", uselist=False,
                           cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="buyer")
    burns = relationship("BurnCertificate", back_populates="user")
    generator_profile = relationship(
        "GeneratorProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    plants = relationship("Plant", back_populates="owner")


# ---------------------------------------------------------------------------
# GENERATOR_PROFILES - Cadastro de geradores (PF/PJ) e aceite de cessao
# ---------------------------------------------------------------------------
class GeneratorProfile(Base):
    __tablename__ = "generator_profiles"

    profile_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id"),
        unique=True,
        nullable=False,
        index=True,
    )
    person_type = Column(String(2), nullable=False, comment="PF | PJ")
    document_id = Column(String(32), unique=True, nullable=False, comment="CPF/CNPJ normalizado")
    legal_name = Column(String(255), nullable=True, comment="Razao social (PJ)")
    trade_name = Column(String(255), nullable=True, comment="Nome fantasia")
    phone = Column(String(30), nullable=True)
    attribute_assignment_accepted = Column(
        Boolean,
        nullable=False,
        default=False,
        comment="Aceite da cessao do atributo ambiental da energia",
    )
    assignment_accepted_at = Column(DateTime, nullable=True)
    onboarding_status = Column(
        String(30),
        nullable=False,
        default="draft",
        comment="draft | profile_completed | integration_pending | active | suspended",
    )
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="generator_profile")
    inverter_connections = relationship(
        "GeneratorInverterConnection",
        back_populates="profile",
        cascade="all, delete-orphan",
    )


class GeneratorInverterConnection(Base):
    __tablename__ = "generator_inverter_connections"

    connection_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("generator_profiles.profile_id"),
        nullable=False,
        index=True,
    )
    plant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("plants.plant_id"),
        nullable=True,
        index=True,
    )
    provider_name = Column(String(100), nullable=False, comment="Marca/plataforma do inversor")
    integration_mode = Column(
        String(30),
        nullable=False,
        comment="direct_api | vendor_partner",
    )
    external_account_ref = Column(String(255), nullable=True, comment="Conta/tenant no provedor")
    inverter_serial = Column(String(100), nullable=True)
    consent_accepted = Column(
        Boolean,
        nullable=False,
        default=False,
        comment="Aceite para leitura de dados de geracao",
    )
    consented_at = Column(DateTime, nullable=True)
    connection_status = Column(
        String(20),
        nullable=False,
        default="pending",
        comment="pending | connected | failed | revoked",
    )
    last_sync_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    profile = relationship("GeneratorProfile", back_populates="inverter_connections")
    plant = relationship("Plant", back_populates="generator_connections")


# ---------------------------------------------------------------------------
# WALLETS - Carteira digital do usuario
# ---------------------------------------------------------------------------
class Wallet(Base):
    __tablename__ = "wallets"

    wallet_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"),
                     unique=True, nullable=False)
    balance_brl = Column(Numeric(16, 2), nullable=False, default=0,
                          comment="Saldo em BRL (centavos de precisão)")
    hec_balance = Column(Integer, nullable=False, default=0,
                          comment="Total de HECs comprados")
    energy_balance_kwh = Column(Numeric(16, 4), nullable=False, default=0,
                                 comment="Total kWh de energia em carteira")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="wallet")


# ---------------------------------------------------------------------------
# TRANSACTIONS — Transações de compra de HECs
# ---------------------------------------------------------------------------
class Transaction(Base):
    __tablename__ = "transactions"

    tx_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    buyer_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"),
                      nullable=False, index=True)
    lot_id = Column(UUID(as_uuid=True), ForeignKey("hec_lots.lot_id"),
                    nullable=False, index=True)
    quantity = Column(Integer, nullable=False,
                      comment="Qtde de HECs comprados nesta transação")
    energy_kwh = Column(Numeric(16, 4), nullable=False,
                         comment="Total kWh da transação")
    unit_price_brl = Column(Numeric(10, 4), nullable=False,
                             comment="Preço por kWh no momento da compra")
    total_price_brl = Column(Numeric(16, 2), nullable=False,
                              comment="Valor total da transação em BRL")
    status = Column(String(20), nullable=False, default="completed",
                    comment="completed | cancelled | refunded")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    buyer = relationship("User", back_populates="transactions")
    lot = relationship("HECLot", back_populates="transactions")


# ---------------------------------------------------------------------------
# BURN_CERTIFICATES — Certificados de queima (aposentadoria) de HECs
# ---------------------------------------------------------------------------
class BurnCertificate(Base):
    __tablename__ = "burn_certificates"

    burn_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"),
                     nullable=False, index=True)
    quantity = Column(Integer, nullable=False,
                      comment="Qtde de HECs queimados")
    energy_kwh = Column(Numeric(16, 4), nullable=False,
                         comment="Total kWh queimado")
    certificate_json = Column(JSONB, nullable=True,
                               comment="JSON canônico do burn certificate")
    hash_sha256 = Column(String(64), unique=True, nullable=False,
                          comment="SHA-256 do JSON canônico")
    # IPFS
    ipfs_json_cid = Column(String(100), nullable=True,
                            comment="CID do JSON no IPFS")
    ipfs_pdf_cid = Column(String(100), nullable=True,
                           comment="CID do PDF no IPFS")
    ipfs_provider = Column(String(20), nullable=True)
    # On-chain
    registry_tx_hash = Column(String(66), nullable=True,
                               comment="Tx hash do registro on-chain do burn")
    registry_block = Column(Integer, nullable=True)
    contract_address = Column(String(42), nullable=True)
    chain = Column(String(20), nullable=True, default="polygon-amoy")
    # HECs burned (list of hec_ids)
    burned_hec_ids = Column(JSONB, nullable=True,
                             comment="Lista de hec_ids queimados")
    status = Column(String(20), nullable=False, default="burned",
                    comment="burned (irreversível)")
    reason = Column(Text, nullable=True,
                    comment="Motivo do burn: offset | retirement | voluntary")
    burned_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="burns")


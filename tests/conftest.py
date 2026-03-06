"""
Fixtures de teste — usa SQLite em memória para testes unitários.
Para testes de integração com TimescaleDB, usar docker-compose.
"""
import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID

from app.db.session import Base, get_db
from app.main import app
from app.models.models import Plant
from app.security import generate_ecdsa_keypair

# SQLite in-memory para testes (sem TimescaleDB)
TEST_DB_URL = "sqlite:///./test.db"
engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

SEED_PLANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@compiles(JSONB, "sqlite")
def _sqlite_jsonb(_type, _compiler, **_kwargs):
    return "JSON"


@compiles(PGUUID, "sqlite")
def _sqlite_uuid(_type, _compiler, **_kwargs):
    return "CHAR(36)"


@pytest.fixture(scope="function")
def db_session():
    """Cria tabelas frescas para cada teste."""
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()

    # Seed: 1 planta
    plant = Plant(
        plant_id=SEED_PLANT_ID,
        name="Usina Teste ABSOLAR",
        absolar_id="TEST-001",
        owner_name="Teste",
        lat=-23.55,
        lng=-46.63,
        capacity_kw=75.0,
        status="active",
    )
    session.add(plant)
    session.commit()

    yield session

    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(db_session):
    """TestClient do FastAPI com DB de teste injetado."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(scope="session")
def ecdsa_keys():
    """Par de chaves ECDSA (secp256k1) para toda a sessão de testes."""
    private_pem, public_pem = generate_ecdsa_keypair()
    return private_pem, public_pem

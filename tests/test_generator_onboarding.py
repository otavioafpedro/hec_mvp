import uuid

from app.models.models import (
    ConsumerProfile,
    GeneratorInverterConnection,
    GeneratorProfile,
    Plant,
    User,
    UserRoleBinding,
    Wallet,
)


def _register_payload(email: str, document_id: str = "12345678901"):
    return {
        "email": email,
        "name": "Gerador Teste",
        "password": "test123",
        "person_type": "PF",
        "document_id": document_id,
        "attribute_assignment_accepted": True,
        "plant": {
            "name": f"Usina {uuid.uuid4().hex[:6]}",
            "absolar_id": None,
            "lat": -23.55,
            "lng": -46.63,
            "capacity_kw": 120.5,
            "inverter_brand": "Growatt",
            "inverter_model": "MID 25KTL3-X",
        },
        "inverter_connection": {
            "provider_name": "growatt",
            "integration_mode": "direct_api",
            "external_account_ref": "acct-demo-001",
            "inverter_serial": "INV-ABC-001",
            "consent_accepted": True,
        },
    }


def _auth_header(token: str):
    return {"Authorization": f"Bearer {token}"}


def test_generator_register_201(client, db_session):
    payload = _register_payload(email=f"gen_{uuid.uuid4().hex[:6]}@test.com")
    r = client.post("/generator-onboarding/register", json=payload)

    assert r.status_code == 201
    data = r.json()
    assert data["role"] == "seller"
    assert data["person_type"] == "PF"
    assert data["onboarding_status"] == "integration_pending"
    assert data["token"]
    assert len(data["connections"]) == 1
    assert data["connections"][0]["connection_status"] == "pending"

    user = db_session.query(User).filter(User.email == payload["email"]).first()
    assert user is not None
    assert user.role == "seller"

    wallet = db_session.query(Wallet).filter(Wallet.user_id == user.user_id).first()
    assert wallet is not None

    profile = (
        db_session.query(GeneratorProfile)
        .filter(GeneratorProfile.user_id == user.user_id)
        .first()
    )
    assert profile is not None
    assert profile.document_id == payload["document_id"]
    assert profile.attribute_assignment_accepted is True

    plant = db_session.query(Plant).filter(Plant.owner_user_id == user.user_id).first()
    assert plant is not None
    assert float(plant.capacity_kw) == payload["plant"]["capacity_kw"]

    connection = (
        db_session.query(GeneratorInverterConnection)
        .filter(GeneratorInverterConnection.profile_id == profile.profile_id)
        .first()
    )
    assert connection is not None
    assert connection.provider_name == "growatt"

    consumer_profile = (
        db_session.query(ConsumerProfile)
        .filter(ConsumerProfile.user_id == user.user_id)
        .first()
    )
    assert consumer_profile is not None
    assert consumer_profile.person_type == "PF"

    bindings = {
        binding.role_code: binding
        for binding in (
            db_session.query(UserRoleBinding)
            .filter(UserRoleBinding.user_id == user.user_id)
            .all()
        )
    }
    assert set(bindings) == {"consumer", "generator"}
    assert bindings["generator"].is_primary is True
    assert bindings["consumer"].is_primary is False


def test_generator_register_duplicate_document_409(client, db_session):
    doc = "11122233344"
    r1 = client.post(
        "/generator-onboarding/register",
        json=_register_payload(email=f"gen_a_{uuid.uuid4().hex[:6]}@test.com", document_id=doc),
    )
    assert r1.status_code == 201

    r2 = client.post(
        "/generator-onboarding/register",
        json=_register_payload(email=f"gen_b_{uuid.uuid4().hex[:6]}@test.com", document_id=doc),
    )
    assert r2.status_code == 409
    assert "ja cadastrado" in r2.json()["detail"]


def test_generator_get_me_200(client, db_session):
    reg = client.post(
        "/generator-onboarding/register",
        json=_register_payload(email=f"gen_me_{uuid.uuid4().hex[:6]}@test.com"),
    )
    assert reg.status_code == 201
    token = reg.json()["token"]

    r = client.get("/generator-onboarding/me", headers=_auth_header(token))
    assert r.status_code == 200
    data = r.json()
    assert data["email"].startswith("gen_me_")
    assert data["onboarding_status"] == "integration_pending"
    assert data["plant_id"] is not None
    assert len(data["connections"]) == 1


def test_generator_add_connection_201(client, db_session):
    reg = client.post(
        "/generator-onboarding/register",
        json=_register_payload(email=f"gen_conn_{uuid.uuid4().hex[:6]}@test.com"),
    )
    assert reg.status_code == 201
    token = reg.json()["token"]

    add = client.post(
        "/generator-onboarding/connections",
        headers=_auth_header(token),
        json={
            "provider_name": "solis",
            "integration_mode": "vendor_partner",
            "external_account_ref": "partner-tenant-09",
            "inverter_serial": "SOLIS-123",
            "consent_accepted": True,
        },
    )
    assert add.status_code == 201
    data = add.json()
    assert data["provider_name"] == "solis"
    assert data["connection_status"] == "pending"

    me = client.get("/generator-onboarding/me", headers=_auth_header(token))
    assert me.status_code == 200
    assert len(me.json()["connections"]) == 2


def test_generator_add_connection_without_consent_422(client, db_session):
    reg = client.post(
        "/generator-onboarding/register",
        json=_register_payload(email=f"gen_noconsent_{uuid.uuid4().hex[:6]}@test.com"),
    )
    assert reg.status_code == 201
    token = reg.json()["token"]

    add = client.post(
        "/generator-onboarding/connections",
        headers=_auth_header(token),
        json={
            "provider_name": "solis",
            "integration_mode": "vendor_partner",
            "consent_accepted": False,
        },
    )
    assert add.status_code == 422
    assert "consent_accepted" in add.json()["detail"]


def test_activate_generator_profile_for_existing_consumer_201(client, db_session):
    reg = client.post(
        "/marketplace/register",
        json={
            "email": f"consumer_{uuid.uuid4().hex[:6]}@test.com",
            "name": "Consumidor",
            "password": "test123",
        },
    )
    assert reg.status_code == 201
    token = reg.json()["token"]
    user_id = reg.json()["user_id"]

    activate = client.post(
        "/generator-onboarding/activate",
        headers=_auth_header(token),
        json={
            "person_type": "PF",
            "document_id": "98765432100",
            "phone": "+55 11 90000-0000",
            "attribute_assignment_accepted": True,
            "plant": {
                "name": f"Usina Ativada {uuid.uuid4().hex[:5]}",
                "lat": -22.90,
                "lng": -43.20,
                "capacity_kw": 55.0,
            },
            "inverter_connection": {
                "provider_name": "sungrow",
                "integration_mode": "vendor_partner",
                "consent_accepted": True,
            },
        },
    )
    assert activate.status_code == 201
    data = activate.json()
    assert data["user_id"] == user_id
    assert data["person_type"] == "PF"
    assert data["onboarding_status"] == "integration_pending"
    assert len(data["connections"]) == 1

    me = client.get("/generator-onboarding/me", headers=_auth_header(token))
    assert me.status_code == 200
    assert me.json()["user_id"] == user_id

    bindings = {
        binding.role_code: binding
        for binding in (
            db_session.query(UserRoleBinding)
            .filter(UserRoleBinding.user_id == uuid.UUID(user_id))
            .all()
        )
    }
    assert set(bindings) == {"consumer", "generator"}
    assert bindings["consumer"].is_primary is True
    assert bindings["generator"].is_primary is False


def test_activate_generator_profile_twice_409(client, db_session):
    reg = client.post(
        "/marketplace/register",
        json={
            "email": f"consumer2_{uuid.uuid4().hex[:6]}@test.com",
            "name": "Consumidor 2",
            "password": "test123",
        },
    )
    assert reg.status_code == 201
    token = reg.json()["token"]

    payload = {
        "person_type": "PF",
        "document_id": "12312312399",
        "attribute_assignment_accepted": True,
        "plant": {
            "name": "Usina Repeticao",
            "lat": -23.0,
            "lng": -46.0,
            "capacity_kw": 70.0,
        },
        "inverter_connection": {
            "provider_name": "growatt",
            "integration_mode": "direct_api",
            "consent_accepted": True,
        },
    }

    first = client.post("/generator-onboarding/activate", headers=_auth_header(token), json=payload)
    assert first.status_code == 201

    second = client.post("/generator-onboarding/activate", headers=_auth_header(token), json=payload)
    assert second.status_code == 409
    assert "ja possui perfil de gerador" in second.json()["detail"]

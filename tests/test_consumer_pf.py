import uuid


def _register_user(client):
    email = f"pf_{uuid.uuid4().hex[:8]}@test.com"
    resp = client.post(
        "/marketplace/register",
        json={
            "email": email,
            "name": "Ana PF",
            "password": "test123",
        },
    )
    assert resp.status_code == 201
    return resp.json()


def _auth(token: str):
    return {"Authorization": f"Bearer {token}"}


def test_pf_dashboard_bootstrap_200(client):
    reg = _register_user(client)
    token = reg["token"]

    r = client.get("/consumer/pf/dashboard", headers=_auth(token))
    assert r.status_code == 200
    data = r.json()

    assert data["user"]["email"] == reg["email"]
    assert "consumer" in data["user"]["roles"]
    assert data["dnft"]["tier"] >= 1
    assert isinstance(data["achievements"], list)
    assert isinstance(data["monthly_footprint"], list)
    assert isinstance(data["leaderboard"], list)


def test_pf_profile_upsert_200(client):
    reg = _register_user(client)
    token = reg["token"]

    upsert = client.put(
        "/consumer/pf/profile",
        headers=_auth(token),
        json={
            "person_type": "PF",
            "document_id": "123.456.789-01",
            "display_name": "Ana Luisa",
            "avatar_seed": "AL",
            "plan_name": "Ouro Verde",
        },
    )
    assert upsert.status_code == 200
    profile = upsert.json()
    assert profile["person_type"] == "PF"
    assert profile["avatar"] == "AL"
    assert profile["name"] == "Ana Luisa"


def test_pf_simulate_retirement_unlocks_progress(client):
    reg = _register_user(client)
    token = reg["token"]

    sim = client.post(
        "/consumer/pf/retirements/simulate",
        headers=_auth(token),
        json={"amount_mhec": 320, "consumed_kwh": 400},
    )
    assert sim.status_code == 200
    sim_data = sim.json()
    assert sim_data["total_retired_mhec"] == 320
    assert sim_data["points_delta"] > 0

    dashboard = client.get("/consumer/pf/dashboard", headers=_auth(token))
    assert dashboard.status_code == 200
    payload = dashboard.json()

    assert payload["user"]["total_retired_mhec"] == 320
    assert payload["user"]["level"] >= 7
    assert payload["dnft"]["mhecs_to_evolve"] >= 0

    done_codes = {item["code"] for item in payload["achievements"] if item["done"]}
    assert "FIRST_RETIREMENT" in done_codes
    assert "RETIRE_100" in done_codes
    assert "RETIRE_300" in done_codes

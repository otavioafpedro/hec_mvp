from fastapi.testclient import TestClient

from app.config import settings
from app.db.soa_session import get_mysql_db, get_timeseries_db
from app.main import app


class _FakeResult:
    def __init__(self, row=None):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _FakeMySQLSession:
    def __init__(self, device_row):
        self.device_row = device_row
        self.committed = False
        self.rolled_back = False

    def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM devices d" in sql:
            return _FakeResult(self.device_row)
        if "UPDATE devices" in sql:
            return _FakeResult()
        raise AssertionError(f"Unexpected SQL in MySQL fake: {sql}")

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


class _FakeTimeseriesSession:
    def __init__(self):
        self.inserted = None
        self.committed = False
        self.rolled_back = False

    def execute(self, statement, params=None):
        sql = str(statement)
        if "INSERT INTO inverter_telemetry" not in sql:
            raise AssertionError(f"Unexpected SQL in Timeseries fake: {sql}")
        self.inserted = params
        return _FakeResult()

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


def test_inverter_ingest_success():
    previous_flag = settings.SOA_ENABLE_INGEST
    settings.SOA_ENABLE_INGEST = True

    mysql_fake = _FakeMySQLSession(
        device_row={
            "device_id": 10,
            "device_uuid": "00000000-0000-0000-0000-000000000301",
            "site_id": 20,
            "device_type": "inverter",
            "device_status": "online",
            "site_status": "active",
        }
    )
    timeseries_fake = _FakeTimeseriesSession()

    def override_mysql():
        try:
            yield mysql_fake
        finally:
            pass

    def override_ts():
        try:
            yield timeseries_fake
        finally:
            pass

    app.dependency_overrides[get_mysql_db] = override_mysql
    app.dependency_overrides[get_timeseries_db] = override_ts

    with TestClient(app) as client:
        response = client.post(
            "/soa/v1/inverter-telemetry",
            json={
                "device_uuid": "00000000-0000-0000-0000-000000000301",
                "timestamp": "2026-03-02T12:00:00Z",
                "power_ac_w": 5000.0,
                "energy_today_wh": 15000,
            },
        )

    app.dependency_overrides.clear()
    settings.SOA_ENABLE_INGEST = previous_flag

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "accepted"
    assert payload["device_id"] == 10
    assert payload["site_id"] == 20
    assert mysql_fake.committed is True
    assert timeseries_fake.committed is True
    assert timeseries_fake.inserted["device_id"] == 10


def test_inverter_ingest_device_not_found():
    previous_flag = settings.SOA_ENABLE_INGEST
    settings.SOA_ENABLE_INGEST = True

    mysql_fake = _FakeMySQLSession(device_row=None)
    timeseries_fake = _FakeTimeseriesSession()

    def override_mysql():
        try:
            yield mysql_fake
        finally:
            pass

    def override_ts():
        try:
            yield timeseries_fake
        finally:
            pass

    app.dependency_overrides[get_mysql_db] = override_mysql
    app.dependency_overrides[get_timeseries_db] = override_ts

    with TestClient(app) as client:
        response = client.post(
            "/soa/v1/inverter-telemetry",
            json={
                "device_uuid": "00000000-0000-0000-0000-000000009999",
                "timestamp": "2026-03-02T12:00:00Z",
                "power_ac_w": 5000.0,
            },
        )

    app.dependency_overrides.clear()
    settings.SOA_ENABLE_INGEST = previous_flag

    assert response.status_code == 404
    assert timeseries_fake.inserted is None

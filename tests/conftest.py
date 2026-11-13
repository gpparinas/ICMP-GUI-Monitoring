import pytest
from fastapi.testclient import TestClient

from netmon.api import create_app
from netmon.db import Database

PASSWORD = "correct horse battery"


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.delenv("NETMON_ADMIN_PASSWORD", raising=False)
    return create_app(tmp_path, start_engine=False)


@pytest.fixture
def client(app):
    """A client with no admin password set yet."""
    with TestClient(app, base_url="http://testserver") as test_client:
        yield test_client


@pytest.fixture
def admin(client):
    """A client signed in as the admin."""
    response = client.post("/api/setup", json={"password": PASSWORD})
    assert response.status_code == 200
    return client


def monitor_fields(**overrides):
    fields = {
        "name": "Router", "type": "icmp", "target": "192.168.1.1", "group": "Network",
        "interval": 60, "timeout": 5, "retries": 2, "latency_warn_ms": None, "enabled": True,
    }
    fields.update(overrides)
    return fields

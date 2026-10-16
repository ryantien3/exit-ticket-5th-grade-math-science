import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes.health import get_database_check


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def database_up() -> None:
    pass


def database_down() -> None:
    raise ConnectionError("simulated outage")


def test_healthz_returns_ok(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_returns_200_when_database_reachable(client):
    app.dependency_overrides[get_database_check] = lambda: database_up
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readyz_returns_503_when_database_unreachable(client):
    app.dependency_overrides[get_database_check] = lambda: database_down
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}

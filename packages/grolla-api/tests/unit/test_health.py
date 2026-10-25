from fastapi.testclient import TestClient
from grolla_api.app import create_app


async def database_up() -> bool:
    return True


async def database_down() -> bool:
    return False


def test_health_is_ok_when_database_is_up() -> None:
    response = TestClient(create_app(database_up)).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_is_unavailable_when_database_is_down() -> None:
    response = TestClient(create_app(database_down)).get("/health")

    assert response.status_code == 503

import pytest
from fastapi.testclient import TestClient
from grolla.bootstrap import Settings, prepare
from grolla_api.app import create_app

pytestmark = pytest.mark.integration


async def test_health_is_ok_against_a_real_database(database_url: str) -> None:
    container = prepare(Settings(database_url=database_url))
    try:
        response = TestClient(create_app(container.database.ping)).get("/health")
        assert response.status_code == 200
    finally:
        await container.database.dispose()

import pytest
from fastapi.testclient import TestClient
from grolla.bootstrap import ApiSettings, TeamConfig, build_api
from grolla_api.app import create_app
from grolla_api.main import services_from
from pydantic import SecretStr

pytestmark = pytest.mark.integration


async def test_health_is_ok_against_a_real_database(database_url: str) -> None:
    settings = ApiSettings(
        database_url=database_url,
        config_file="/nao-usado.yaml",  # pyright: ignore[reportArgumentType]
        alerts_webhook_user="signoz",
        alerts_webhook_password=SecretStr("segredo"),
    )
    container = build_api(settings, TeamConfig(team="t", page_base_url="http://localhost"))
    try:
        response = TestClient(create_app(services_from(container))).get("/health")
        assert response.status_code == 200
    finally:
        await container.database.dispose()

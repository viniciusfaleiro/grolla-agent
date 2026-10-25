from typing import Any, cast

from fastapi.testclient import TestClient
from grolla.testing.clock import FakeClock
from grolla_api.app import Services, create_app


async def database_up() -> bool:
    return True


async def database_down() -> bool:
    return False


async def no_heartbeat() -> None:
    return None


def services(database_is_up: Any) -> Services:
    unused = cast(Any, None)  # as rotas de saúde não usam os ports de incidentes
    return Services(
        database_is_up=database_is_up,
        latest_heartbeat=no_heartbeat,
        ingress=unused,
        repository=unused,
        intake=unused,
        clock=FakeClock(),
    )


def test_health_is_ok_when_database_is_up() -> None:
    response = TestClient(create_app(services(database_up))).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_is_unavailable_when_database_is_down() -> None:
    response = TestClient(create_app(services(database_down))).get("/health")

    assert response.status_code == 503

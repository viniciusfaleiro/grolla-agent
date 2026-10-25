from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from grolla.testing.clock import FakeClock
from grolla_api.app import Services, create_app


async def database_down() -> bool:
    return False


def heartbeat_at(at: datetime) -> Callable[[], Awaitable[datetime | None]]:
    async def latest() -> datetime | None:
        return at

    return latest


def worker_health(services: Services, age: timedelta) -> tuple[int, object, datetime]:
    clock = FakeClock()
    last = clock.now() - age
    app = create_app(replace(services, latest_heartbeat=heartbeat_at(last), clock=clock))
    response = TestClient(app).get("/health/worker")
    return response.status_code, response.json(), last


def test_health_is_ok_when_database_is_up(services: Services) -> None:
    response = TestClient(create_app(services)).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_is_unavailable_when_database_is_down(services: Services) -> None:
    app = create_app(replace(services, database_is_up=database_down))

    assert TestClient(app).get("/health").status_code == 503


def test_worker_is_alive_with_a_recent_heartbeat(services: Services) -> None:
    code, body, last = worker_health(services, timedelta(seconds=20))

    assert code == 200
    assert body == {"last_heartbeat": last.isoformat()}


def test_worker_is_alive_at_exactly_two_minutes(services: Services) -> None:
    code, _, _ = worker_health(services, timedelta(minutes=2))

    assert code == 200


def test_worker_is_down_with_an_old_heartbeat(services: Services) -> None:
    code, body, last = worker_health(services, timedelta(minutes=5))

    assert code == 503
    assert body == {"last_heartbeat": last.isoformat()}


def test_worker_is_down_without_any_heartbeat(services: Services) -> None:
    response = TestClient(create_app(services)).get("/health/worker")

    assert response.status_code == 503
    assert response.json() == {"last_heartbeat": None}

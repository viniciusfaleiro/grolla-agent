from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from fastapi import FastAPI, Response, status
from grolla.application.clock import Clock
from grolla.application.incidents import AlertIngress, IncidentRepository, IntakePolicy

HealthCheck = Callable[[], Awaitable[bool]]
LatestHeartbeat = Callable[[], Awaitable[datetime | None]]


@dataclass(frozen=True)
class Services:
    """O que as rotas usam; nos testes, montado com os fakes dos ports."""

    database_is_up: HealthCheck
    latest_heartbeat: LatestHeartbeat
    ingress: AlertIngress
    repository: IncidentRepository
    intake: IntakePolicy
    clock: Clock


def create_app(services: Services) -> FastAPI:
    app = FastAPI(title="agent-grolla")

    @app.get("/health")
    async def health(response: Response) -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        if await services.database_is_up():
            return {"status": "ok"}
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable"}

    return app

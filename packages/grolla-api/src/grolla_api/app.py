from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Response, status

HealthCheck = Callable[[], Awaitable[bool]]


def create_app(database_is_up: HealthCheck) -> FastAPI:
    app = FastAPI(title="agent-grolla")

    @app.get("/health")
    async def health(response: Response) -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        if await database_is_up():
            return {"status": "ok"}
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable"}

    return app

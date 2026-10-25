"""App dos fakes.

O runner do e2e controla o estado por `/_control/*`. Todo o resto é a superfície fingindo ser os
serviços externos: cada requisição é gravada e respondida pelo roteiro do cenário (404 se o
roteiro não prevê aquela rota). Os slices de cada fornecedor acrescentam aqui o protocolo dele.
"""

from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

CONTROL_PREFIX = "/_control"


class ScriptedRoute(BaseModel):
    method: str
    path: str
    status: int = 200
    body: Any = None


class Scenario(BaseModel):
    routes: list[ScriptedRoute] = []


class RecordedRequest(BaseModel):
    method: str
    path: str
    body: Any = None


class FakesState:
    def __init__(self) -> None:
        self.scenario = Scenario()
        self.requests: list[RecordedRequest] = []

    def reset(self) -> None:
        self.scenario = Scenario()
        self.requests = []

    def find_route(self, method: str, path: str) -> ScriptedRoute | None:
        for route in self.scenario.routes:
            if route.method.upper() == method.upper() and route.path == path:
                return route
        return None


def create_app() -> FastAPI:
    app = FastAPI(title="grolla-fakes")
    state = FakesState()

    @app.post(f"{CONTROL_PREFIX}/reset", status_code=204)
    async def reset() -> None:  # pyright: ignore[reportUnusedFunction]
        state.reset()

    @app.post(f"{CONTROL_PREFIX}/scenario", status_code=204)
    async def load_scenario(scenario: Scenario) -> None:  # pyright: ignore[reportUnusedFunction]
        state.scenario = scenario

    @app.get(f"{CONTROL_PREFIX}/requests")
    async def recorded_requests() -> list[RecordedRequest]:  # pyright: ignore[reportUnusedFunction]
        return state.requests

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def fake(path: str, request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        raw = await request.body()
        body: Any = await request.json() if raw else None
        state.requests.append(RecordedRequest(method=request.method, path=f"/{path}", body=body))
        route = state.find_route(request.method, f"/{path}")
        if route is None:
            return JSONResponse({"error": "rota fora do roteiro do cenário"}, status_code=404)
        return JSONResponse(route.body, status_code=route.status)

    return app

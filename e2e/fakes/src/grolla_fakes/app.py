"""App dos fakes.

O runner do e2e controla o estado por `/_control/*`. Todo o resto é a superfície fingindo ser os
serviços externos: cada requisição é gravada e respondida pelo roteiro do cenário (404 se o
roteiro não prevê aquela rota). Os slices de cada fornecedor acrescentam aqui o protocolo dele.
"""

from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from grolla_fakes.azure_devops import AzureDevOpsFake
from grolla_fakes.console import ApiTarget, console_router
from grolla_fakes.llm import llm_router
from grolla_fakes.signoz_mcp import SignozFake
from grolla_fakes.state import FakesState, RecordedRequest, Scenario

CONTROL_PREFIX = "/_control"


def create_app(api: ApiTarget | None = None) -> FastAPI:
    app = FastAPI(title="grolla-fakes")
    state = FakesState()
    app.include_router(console_router(state, api or ApiTarget.from_env()))
    app.include_router(llm_router(state))
    signoz, azure_devops = SignozFake(state), AzureDevOpsFake(state)
    app.include_router(signoz.control_router())
    app.mount("/signoz", signoz.asgi)  # o servidor MCP atende em /signoz/mcp
    app.include_router(azure_devops.router())

    @app.post(f"{CONTROL_PREFIX}/reset", status_code=204)
    async def reset() -> None:  # pyright: ignore[reportUnusedFunction]
        state.reset()
        signoz.reset()
        azure_devops.reset()

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
        route = state.find_route(request.method, f"/{path}")
        status = 404 if route is None else route.status
        state.requests.append(
            RecordedRequest(method=request.method, path=f"/{path}", body=body, status=status)
        )
        if route is None:
            return JSONResponse({"error": "rota fora do roteiro do cenário"}, status_code=status)
        return JSONResponse(route.body, status_code=status)

    return app

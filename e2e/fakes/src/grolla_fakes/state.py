"""Estado em memória dos fakes: roteiro do cenário, requisições gravadas e alertas do console."""

from typing import Any

from pydantic import BaseModel


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
    status: int  # o que o fake respondeu: o console distingue card entregue de recusado


class SentAlert(BaseModel):
    """Alerta disparado pelo console, guardado para poder ser resolvido depois."""

    fingerprint: str
    starts_at: str
    labels: dict[str, str]
    annotations: dict[str, str]
    status: str  # firing | resolved
    api_status: int


DEFAULT_LLM_MODE = "completa"


class FakesState:
    def __init__(self) -> None:
        self.scenario = Scenario()
        self.requests: list[RecordedRequest] = []
        self.console_alerts: list[SentAlert] = []
        self.llm_mode = DEFAULT_LLM_MODE  # modo da Messages API fake (grolla_fakes.llm)
        self.llm_verification_mode = DEFAULT_LLM_MODE  # o mesmo, na fase de validação

    def reset(self) -> None:
        self.scenario = Scenario()
        self.requests = []
        self.console_alerts = []
        self.llm_mode = DEFAULT_LLM_MODE
        self.llm_verification_mode = DEFAULT_LLM_MODE

    def set_route(self, route: ScriptedRoute) -> None:
        """Troca (ou acrescenta) a rota de mesmo método e caminho no roteiro."""
        others = [
            r
            for r in self.scenario.routes
            if not (r.method.upper() == route.method.upper() and r.path == route.path)
        ]
        self.scenario = Scenario(routes=[*others, route])

    def find_route(self, method: str, path: str) -> ScriptedRoute | None:
        for route in self.scenario.routes:
            if route.method.upper() == method.upper() and route.path == path:
                return route
        return None

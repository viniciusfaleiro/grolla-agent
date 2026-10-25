"""Console manual de testes: dispara alertas como o SigNoz e mostra os cards como o Teams.

A página chama só o próprio fake. É o fake que chama o webhook da api, com as credenciais do
ambiente, para o navegador não precisar de CORS nem ver a senha.
"""

import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx2
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from grolla_fakes.state import FakesState, ScriptedRoute, SentAlert

CONSOLE_PREFIX = "/_console"
TEAMS_PATH = "/teams/webhook"
NO_END = "0001-01-01T00:00:00Z"  # o Alertmanager manda isso para alerta ainda disparando
PAGE = Path(__file__).parent / "console.html"


@dataclass(frozen=True)
class ApiTarget:
    """Onde está o webhook de alertas da api e com que credenciais chamá-lo."""

    url: str = "http://localhost:8000"
    user: str = "signoz"
    password: str = "segredo-do-e2e"  # noqa: S105 — credencial fixa do stack de testes
    transport: httpx2.AsyncBaseTransport | None = None  # testes injetam a api fake

    @classmethod
    def from_env(cls) -> "ApiTarget":
        defaults = cls()
        return cls(
            url=os.environ.get("GROLLA_FAKES_API_URL", defaults.url),
            user=os.environ.get("GROLLA_FAKES_API_USER", defaults.user),
            password=os.environ.get("GROLLA_FAKES_API_PASSWORD", defaults.password),
        )

    async def send(self, alert: SentAlert) -> tuple[int, str]:
        try:
            async with httpx2.AsyncClient(transport=self.transport, timeout=10) as client:
                response = await client.post(
                    f"{self.url}/webhooks/alerts",
                    json=signoz_payload(alert),
                    auth=(self.user, self.password),
                )
        except httpx2.HTTPError as error:
            raise HTTPException(502, f"api fora do ar: {type(error).__name__}") from error
        return response.status_code, response.text


class AlertForm(BaseModel):
    alertname: str = Field(min_length=1)
    service: str = Field(min_length=1)
    severity: str = "warning"
    team: str = "credenciadora"
    summary: str = ""
    description: str = ""


class TeamsMode(BaseModel):
    status: Literal[202, 500]


def signoz_payload(alert: SentAlert) -> dict[str, Any]:
    """Mesmo formato que o canal de webhook do SigNoz manda (compatível com Alertmanager)."""
    return {
        "receiver": "grolla-console",
        "status": alert.status,
        "version": "4",
        "alerts": [
            {
                "status": alert.status,
                "labels": alert.labels,
                "annotations": alert.annotations,
                "startsAt": alert.starts_at,
                "endsAt": NO_END if alert.status == "firing" else _now(),
                "generatorURL": "",
                "fingerprint": alert.fingerprint,
            }
        ],
    }


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def console_router(state: FakesState, api: ApiTarget) -> APIRouter:
    router = APIRouter(prefix=CONSOLE_PREFIX)

    def teams_status() -> int | None:
        route = state.find_route("POST", TEAMS_PATH)
        return None if route is None else route.status

    def set_teams(status: int) -> None:
        state.set_route(ScriptedRoute(method="POST", path=TEAMS_PATH, status=status, body={}))

    @router.get("", response_class=HTMLResponse)
    async def page() -> str:  # pyright: ignore[reportUnusedFunction]
        if teams_status() is None:
            set_teams(202)  # sem roteiro, o Teams do console começa aceitando
        return PAGE.read_text(encoding="utf-8")

    @router.get("/state")
    async def console_state() -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        return {
            "teams_status": teams_status(),
            "alerts": [a.model_dump() for a in reversed(state.console_alerts)],
            "messages": [
                {"status": r.status, "message": r.body}
                for r in state.requests
                if r.method == "POST" and r.path == TEAMS_PATH
            ],
        }

    @router.post("/teams", status_code=204)
    async def teams_mode(mode: TeamsMode) -> None:  # pyright: ignore[reportUnusedFunction]
        set_teams(mode.status)

    @router.post("/alerts")
    async def fire(form: AlertForm) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        alert = SentAlert(
            fingerprint=uuid.uuid4().hex[:16],
            starts_at=_now(),
            labels={
                "alertname": form.alertname,
                "service_name": form.service,
                "severity": form.severity,
                "team": form.team,
            },
            annotations={"summary": form.summary, "description": form.description},
            status="firing",
            api_status=0,
        )
        return await _send(alert)

    @router.post("/alerts/{fingerprint}/resolve")
    async def resolve(fingerprint: str) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        fired = next((a for a in state.console_alerts if a.fingerprint == fingerprint), None)
        if fired is None:
            raise HTTPException(404, "alerta não foi disparado por este console")
        return await _send(fired.model_copy(update={"status": "resolved"}))

    async def _send(alert: SentAlert) -> dict[str, Any]:
        status, body = await api.send(alert)
        sent = alert.model_copy(update={"api_status": status})
        state.console_alerts = [
            a for a in state.console_alerts if a.fingerprint != alert.fingerprint
        ] + [sent]
        return {"api_status": status, "api_body": body}

    return router

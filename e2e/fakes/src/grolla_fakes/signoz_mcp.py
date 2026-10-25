"""Servidor MCP fake do SigNoz em `/signoz/mcp`, feito com a lib `mcp`.

Expõe só as ferramentas de leitura que o adapter usa, com os mesmos nomes e respostas no formato
do Query Builder v5. Sem roteiro, conta uma história plausível e coerente com o deploy padrão do
Azure DevOps fake (há 20 min): a partir de 18 min atrás a taxa de erro e a latência sobem e os
logs mostram timeout de conexão com o banco.

O roteiro (`POST /_control/signoz`) fixa a resposta de uma ferramenta ou a faz falhar.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter
from mcp.server.lowlevel import Server
from mcp.types import CallToolRequestParams, CallToolResult, ListToolsResult, TextContent, Tool
from pydantic import BaseModel

from grolla_fakes.state import FakesState, RecordedRequest

MCP_PATH = "/signoz/mcp"
ONSET = timedelta(minutes=18)  # os erros começam logo depois do deploy padrão (20 min)
DEFAULT_STEP_SECONDS = 600
OK_SPANS_PER_10_MIN = 1200
ERROR_SPANS_BEFORE, ERROR_SPANS_AFTER = 5, 90
P99_BEFORE_NS, P99_AFTER_NS = 180_000_000, 2_400_000_000
CALLS_PER_SECOND = 20.0
ERROR_LOGS = 31
ERROR_SPANS = 12

_ANY: dict[str, Any] = {"type": ["string", "number", "boolean"]}
TOOLS = {
    "signoz_aggregate_traces": ["aggregation", "aggregateOn", "groupBy", "service", "error"],
    "signoz_search_logs": ["service", "filter", "searchText", "severity", "limit"],
    "signoz_search_traces": ["service", "error", "operation", "limit"],
    "signoz_get_service_top_operations": ["service"],
}
_WINDOW = ["start", "end", "timeRange", "requestType", "stepInterval"]


class SignozScript(BaseModel):
    responses: dict[str, Any] = {}  # resposta fixa por ferramenta (vira o texto JSON)
    failing: list[str] = []  # ferramentas que respondem erro


class SignozFake:
    def __init__(self, state: FakesState) -> None:
        self._state = state
        self.script = SignozScript()
        self._server = Server("signoz-fake", on_list_tools=self._list, on_call_tool=self._call)

    def reset(self) -> None:
        self.script = SignozScript()

    def control_router(self) -> APIRouter:
        router = APIRouter()

        @router.post("/_control/signoz", status_code=204)
        async def load(script: SignozScript) -> None:  # pyright: ignore[reportUnusedFunction]
            self.script = script

        return router

    async def asgi(self, scope: Any, receive: Any, send: Any) -> None:
        """Sem estado: cada requisição HTTP sobe e desce o próprio gerenciador de sessões."""
        app = self._server.streamable_http_app(
            stateless_http=True,
            json_response=True,
            host="0.0.0.0",  # noqa: S104 — fora de localhost: o worker chama fakes:9000
        )
        manager = self._server.session_manager  # o desta requisição, antes de qualquer await
        async with manager.run():
            await app(scope, receive, send)

    async def _list(self, _ctx: Any, _params: Any) -> ListToolsResult:
        return ListToolsResult(
            tools=[
                Tool(
                    name=name,
                    input_schema={
                        "type": "object",
                        "properties": dict.fromkeys([*arguments, *_WINDOW], _ANY),
                    },
                )
                for name, arguments in TOOLS.items()
            ]
        )

    async def _call(self, _ctx: Any, params: CallToolRequestParams) -> CallToolResult:
        arguments = params.arguments or {}
        failed = params.name in self.script.failing or params.name not in TOOLS
        self._state.requests.append(
            RecordedRequest(
                method="POST",
                path=MCP_PATH,
                body={"tool": params.name, "arguments": arguments},
                status=500 if failed else 200,
            )
        )
        if failed:
            return CallToolResult(
                content=[TextContent(text=f"ferramenta {params.name} indisponível")],
                is_error=True,
            )
        if params.name in self.script.responses:
            answer = self.script.responses[params.name]
        else:
            answer = _default(params.name, arguments, datetime.now(UTC))
        return CallToolResult(content=[TextContent(text=json.dumps(answer))])


# ---- História padrão


def _default(tool: str, arguments: dict[str, Any], now: datetime) -> Any:
    start, end = _window(arguments, now)
    service = str(arguments.get("service") or "servico")
    onset = now - ONSET
    if tool == "signoz_aggregate_traces":
        return _aggregate(arguments, start, end, onset)
    if tool == "signoz_search_logs":
        return _logs(arguments, service, start, end, onset)
    if tool == "signoz_search_traces":
        return _traces(service, start, end, onset)
    return _top_operations()


def _window(arguments: dict[str, Any], now: datetime) -> tuple[datetime, datetime]:
    def moment(key: str, fallback: datetime) -> datetime:
        value = arguments.get(key)
        return datetime.fromtimestamp(int(value) / 1000, UTC) if value else fallback

    return moment("start", now - timedelta(hours=1)), moment("end", now)


def _aggregate(
    arguments: dict[str, Any], start: datetime, end: datetime, onset: datetime
) -> dict[str, Any]:
    step = int(arguments.get("stepInterval") or DEFAULT_STEP_SECONDS)
    buckets: list[datetime] = []
    moment = start
    while moment < end:
        buckets.append(moment)
        moment += timedelta(seconds=step)

    def values(before: float, after: float) -> list[dict[str, Any]]:
        return [
            {
                "timestamp": int(b.timestamp() * 1000),
                "value": after if b + timedelta(seconds=step) > onset else before,
            }
            for b in buckets
        ]

    scale = step / DEFAULT_STEP_SECONDS
    if arguments.get("groupBy") == "has_error":
        series = [
            _labeled(False, values(OK_SPANS_PER_10_MIN * scale, OK_SPANS_PER_10_MIN * scale)),
            _labeled(True, values(ERROR_SPANS_BEFORE * scale, ERROR_SPANS_AFTER * scale)),
        ]
    elif arguments.get("aggregation") == "p99":
        series = [{"values": values(P99_BEFORE_NS, P99_AFTER_NS)}]
    else:
        series = [{"values": values(CALLS_PER_SECOND, CALLS_PER_SECOND)}]
    return _envelope(
        "time_series", {"queryName": "A", "aggregations": [{"index": 0, "series": series}]}
    )


def _labeled(has_error: bool, values: list[dict[str, Any]]) -> dict[str, Any]:
    key = {"name": "has_error", "signal": "traces", "fieldContext": "span"}
    return {"labels": [{"key": key, "value": has_error}], "values": values}


def _logs(
    arguments: dict[str, Any], service: str, start: datetime, end: datetime, onset: datetime
) -> dict[str, Any]:
    body = f"connection timeout to {service}-db:5432 after 5000ms (pool exhausted)"
    text = str(arguments.get("searchText") or "").lower()
    moments = [onset + timedelta(seconds=30 * i) for i in range(ERROR_LOGS)]
    rows = [
        {
            "timestamp": m.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "data": {
                "body": body,
                "severity_text": "ERROR",
                "severity_number": 17,
                "resources_string": {"service.name": service},
            },
        }
        for m in moments
        if start <= m <= end and text in body.lower()
    ]
    return _envelope("raw", {"queryName": "A", "nextCursor": "", "rows": rows})


def _traces(service: str, start: datetime, end: datetime, onset: datetime) -> dict[str, Any]:
    moments = [onset + timedelta(seconds=60 * i) for i in range(ERROR_SPANS)]
    rows = [
        {
            "timestamp": m.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "data": {
                "trace_id": f"{i + 1:032x}",
                "name": "POST /authorize",
                "service.name": service,
                "duration_nano": 5_003_000_000,
                "has_error": True,
                "status_message": "context deadline exceeded: acquire db connection",
            },
        }
        for i, m in enumerate(moments)
        if start <= m <= end
    ]
    return _envelope("raw", {"queryName": "A", "nextCursor": "", "rows": rows})


def _top_operations() -> list[dict[str, Any]]:
    return [
        {"name": "POST /authorize", "p99": 2.4e9, "numCalls": 7183, "errorCount": 234},
        {"name": "POST /capture", "p99": 3.1e8, "numCalls": 1500, "errorCount": 2},
        {"name": "GET /health", "p99": 4e6, "numCalls": 720, "errorCount": 0},
    ]


def _envelope(kind: str, result: dict[str, Any]) -> dict[str, Any]:
    return {"status": "success", "data": {"type": kind, "meta": {}, "data": {"results": [result]}}}

"""Servidor MCP fake do SigNoz em `/signoz/mcp`, feito com a lib `mcp`.

Expõe só as ferramentas de leitura que o adapter usa, com os mesmos nomes e respostas no formato
do Query Builder v5. Sem roteiro, conta uma história plausível e coerente com o deploy padrão do
Azure DevOps fake (há 20 min): a partir de 18 min atrás a taxa de erro e a latência sobem e os
logs mostram timeout de conexão com o banco (ERROR) e a conexão derrubada pelo servidor (WARN,
com o IP variando como no Postgres real).

Para a memória, as regras de alerta trazem uma só, "Taxa de erro alta no estudo-api" (labels
`service_name: estudo-api` e `team: credenciadora`), com a condição em `signoz_get_alert`; os
serviços ativos são `estudo-api` e `authorizer-api`; e a agregação de dependências (com
`groupBy`) diz que cada serviço chama o próprio banco: `estudo-api` → `estudo-db`.

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
WARN_LOGS = 22
ERROR_SPANS = 12

_ANY: dict[str, Any] = {"type": ["string", "number", "boolean"]}
TOOLS = {
    "signoz_aggregate_traces": [
        "aggregation",
        "aggregateOn",
        "groupBy",
        "service",
        "error",
        "filter",
        "limit",
    ],
    "signoz_search_logs": ["service", "filter", "searchText", "severity", "limit"],
    "signoz_search_traces": ["service", "error", "operation", "limit"],
    "signoz_get_service_top_operations": ["service"],
    "signoz_list_alert_rules": ["limit", "offset"],
    "signoz_get_alert": ["id"],
    "signoz_list_services": ["limit", "offset"],
}

# Memória: a regra do domínio do time (label `team`, o `alert_filter` do e2e) e o serviço dela,
# que chama o próprio banco. O id é um UUIDv7 fixo, como os do SigNoz v2.
RULE_ID = "01a17500-0000-7000-8000-00000000e57d"
RULE_SERVICE = "estudo-api"
RULE = {
    "ruleId": RULE_ID,
    "alert": f"Taxa de erro alta no {RULE_SERVICE}",
    "alertType": "TRACES_BASED_ALERT",
    "ruleType": "threshold_rule",
    "state": "inactive",
    "disabled": False,
    "severity": "critical",
    "labels": {"service_name": RULE_SERVICE, "severity": "critical", "team": "credenciadora"},
}
SERVICES = (RULE_SERVICE, "authorizer-api")
DEPENDENCY_CALLS, DEPENDENCY_ERRORS, DEPENDENCY_P99_NS = 4200, 12, 35_000_000
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
    environment = _environment(tool, arguments, service)
    if environment is not None:
        return environment
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
    errors = (ERROR_SPANS_BEFORE * scale, ERROR_SPANS_AFTER * scale)
    if arguments.get("aggregation") == "count" and str(arguments.get("error")).lower() == "true":
        series = [{"values": values(*errors)}]  # como o SigNoz real: só os spans com erro
    elif arguments.get("aggregation") == "count":
        ok = OK_SPANS_PER_10_MIN * scale
        series = [{"values": values(ok + errors[0], ok + errors[1])}]
    elif arguments.get("aggregation") == "p99":
        series = [{"values": values(P99_BEFORE_NS, P99_AFTER_NS)}]
    else:
        series = [{"values": values(CALLS_PER_SECOND, CALLS_PER_SECOND)}]
    return _envelope(
        "time_series", {"queryName": "A", "aggregations": [{"index": 0, "series": series}]}
    )


def _logs(
    arguments: dict[str, Any], service: str, start: datetime, end: datetime, onset: datetime
) -> dict[str, Any]:
    error = f"connection timeout to {service}-db:5432 after 5000ms (pool exhausted)"
    logs = [(onset + timedelta(seconds=30 * i), error, "ERROR", 17) for i in range(ERROR_LOGS)]
    logs += [
        (
            onset + timedelta(seconds=40 * i),
            f'connection to server at "10.0.1.{23 + i % 2}", port 5432 failed: server closed '
            "the connection unexpectedly",
            "WARN",
            13,
        )
        for i in range(WARN_LOGS)
    ]
    text = str(arguments.get("searchText") or "").lower()
    rows = [
        {
            "timestamp": m.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "data": {
                "body": body,
                "severity_text": level,
                "severity_number": number,
                "resources_string": {"service.name": service},
            },
        }
        for m, body, level, number in sorted(logs, reverse=True)
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


def _environment(tool: str, arguments: dict[str, Any], service: str) -> Any:
    """As leituras do estudo do ambiente; None para as outras ferramentas."""
    if tool == "signoz_list_alert_rules":
        return _page([RULE])
    if tool == "signoz_get_alert":
        return _rule_definition()
    if tool == "signoz_list_services":
        return _page([_service_summary(s) for s in SERVICES])
    if tool == "signoz_aggregate_traces" and arguments.get("groupBy"):
        return _dependencies(arguments, service)
    return None


def _page(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Listagem paginada do MCP real: tudo numa página só."""
    pagination = {"total": len(items), "offset": 0, "hasMore": False, "nextOffset": -1}
    return {"data": items, "pagination": pagination}


def _rule_definition() -> dict[str, Any]:
    """A definição completa (`signoz_get_alert`), no formato v5 do SigNoz real."""
    spec = {
        "name": "A",
        "stepInterval": 60,
        "signal": "traces",
        "aggregations": [{"expression": "count()"}],
        "filter": {"expression": f"service.name = '{RULE_SERVICE}' AND has_error = true"},
    }
    condition = {
        "compositeQuery": {"queries": [{"type": "builder_query", "spec": spec}]},
        "op": "1",
        "target": 20,
        "matchType": "1",
        "selectedQueryName": "A",
    }
    summary = f"Spans com erro no {RULE_SERVICE} acima de 20 em 5 min"
    data = {
        **{k: v for k, v in RULE.items() if k != "ruleId"},
        "id": RULE_ID,
        "annotations": {"summary": summary},
        "condition": condition,
        "evalWindow": "5m0s",
        "frequency": "1m0s",
        "version": "v5",
    }
    return {"status": "success", "data": data}


def _service_summary(service: str) -> dict[str, Any]:
    return {"serviceName": service, "numCalls": 50_000, "numErrors": 100, "p99": 2.4e9}


def _dependencies(arguments: dict[str, Any], service: str) -> dict[str, Any]:
    """Spans de saída agrupados: cada serviço chama o próprio banco `<serviço sem -api>-db`."""
    database = service.removesuffix("-api")
    if arguments.get("aggregation") == "p99":
        value: float = DEPENDENCY_P99_NS
    elif str(arguments.get("error")).lower() == "true":
        value = DEPENDENCY_ERRORS
    else:
        value = DEPENDENCY_CALLS
    names = ("peer.service", "db.system", "db.name", "server.address")
    columns = [{"name": n, "queryName": "A", "columnType": "group"} for n in names]
    columns.append({"name": "__result_0", "queryName": "A", "columnType": "aggregation"})
    row = [f"{database}-db", "postgresql", database, None, value]
    return _envelope("scalar", {"queryName": "A", "columns": columns, "data": [row]})


def _envelope(kind: str, result: dict[str, Any]) -> dict[str, Any]:
    return {"status": "success", "data": {"type": kind, "meta": {}, "data": {"results": [result]}}}

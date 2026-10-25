"""SignozTelemetry contra um servidor MCP em processo (lib `mcp`), servido por ASGI sem rede.

As respostas são fixtures no formato do Query Builder v5 do SigNoz; o catálogo das ferramentas
é o recorte do `tools/list` real do `signoz-mcp-server`.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from contracts.telemetry_contract import TelemetryContract
from grolla.adapters.signoz.telemetry import READ_TOOLS, SignozTelemetry
from grolla.application.investigation import Telemetry, ToolUnavailableError
from mcp.server.lowlevel import Server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

FIXTURES = Path(__file__).parent.parent / "fixtures" / "signoz_mcp"
URL = "http://signoz-mcp:8000/mcp"
BASE = "https://signoz.interno"
START = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
END = START + timedelta(hours=1)


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


CATALOG: list[dict[str, Any]] = fixture("tools_list")["tools"]


def span_counts(arguments: dict[str, Any]) -> Any:
    """Como o SigNoz real: o total sem filtro e, com error=true, só os spans com erro."""
    return fixture(
        "aggregate_traces_errors" if arguments.get("error") else "aggregate_traces_count"
    )


def default_answers() -> dict[str, Any]:
    return {
        "signoz_aggregate_traces": span_counts,
        "signoz_search_logs": fixture("search_logs"),
        "signoz_search_traces": fixture("search_traces_errors"),
        "signoz_get_service_top_operations": fixture("top_operations"),
    }


class FakeSignozMcp:
    """Servidor MCP roteirizado: responde por ferramenta e grava as chamadas."""

    def __init__(self) -> None:
        self.answers = default_answers()
        self.failing = False
        self.delay = 0.0
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.server = Server("signoz", on_list_tools=self._list, on_call_tool=self._call)

    async def app(self, scope: Any, receive: Any, send: Any) -> None:
        """Sem estado: cada requisição HTTP sobe e desce o próprio gerenciador de sessões."""
        app = self.server.streamable_http_app(
            stateless_http=True, json_response=True, host="signoz-mcp"
        )
        async with self.server.session_manager.run():
            await app(scope, receive, send)

    async def _list(self, _ctx: Any, _params: Any) -> ListToolsResult:
        return ListToolsResult(tools=[Tool.model_validate(tool) for tool in CATALOG])

    async def _call(self, _ctx: Any, params: Any) -> CallToolResult:
        self.calls.append((params.name, params.arguments or {}))
        await asyncio.sleep(self.delay)
        if self.failing:
            return CallToolResult(content=[TextContent(text="upstream error")], is_error=True)
        answer = self.answers[params.name]
        if callable(answer):
            answer = answer(params.arguments or {})
        text = answer if isinstance(answer, str) else json.dumps(answer)
        return CallToolResult(content=[TextContent(text=text)])

    def telemetry(self, timeout_seconds: float = 5.0) -> SignozTelemetry:
        return SignozTelemetry(URL, BASE, timeout_seconds, httpx2.ASGITransport(app=self.app))


@pytest.fixture
def mcp() -> FakeSignozMcp:
    return FakeSignozMcp()


class TestSignozTelemetryContract(TelemetryContract):
    @pytest.fixture
    def telemetry(self, mcp: FakeSignozMcp) -> Telemetry:
        return mcp.telemetry()

    @pytest.fixture
    def unavailable_telemetry(self, mcp: FakeSignozMcp) -> Telemetry:
        mcp.failing = True
        return mcp.telemetry()


def schema(tool: str) -> dict[str, Any]:
    return next(t["inputSchema"] for t in CATALOG if t["name"] == tool)


def test_read_tools_are_read_only_in_the_real_catalog() -> None:
    annotations = {t["name"]: t["annotations"] for t in CATALOG}
    assert annotations.keys() == READ_TOOLS
    assert all(a["readOnlyHint"] and not a["destructiveHint"] for a in annotations.values())


async def test_write_tool_is_refused_without_calling_the_server(mcp: FakeSignozMcp) -> None:
    with pytest.raises(ToolUnavailableError, match="fora da lista de leitura"):
        await mcp.telemetry().call("signoz_create_dashboard", {"title": "x"})

    assert mcp.calls == []


async def test_every_argument_sent_exists_in_the_tool_schema(mcp: FakeSignozMcp) -> None:
    telemetry = mcp.telemetry()
    await telemetry.query_metric("authorizer-api", "latencia_p99", START, END)
    await telemetry.search_logs("authorizer-api", "timeout", START, END)
    await telemetry.find_traces("authorizer-api", True, START, END)
    await telemetry.find_traces("authorizer-api", False, START, END)

    assert [name for name, _ in mcp.calls] == [
        "signoz_aggregate_traces",
        "signoz_search_logs",
        "signoz_search_traces",
        "signoz_get_service_top_operations",
    ]
    for name, arguments in mcp.calls:
        properties = schema(name)["properties"]
        assert set(arguments) <= set(properties), name
        assert set(schema(name).get("required", [])) <= set(arguments), name


async def test_error_rate_is_a_series_of_percentages(mcp: FakeSignozMcp) -> None:
    result = await mcp.telemetry().query_metric("authorizer-api", "taxa_de_erro", START, END)

    total = {
        "aggregation": "count",
        "service": "authorizer-api",
        "requestType": "time_series",
        "stepInterval": "600",
        "start": str(int(START.timestamp() * 1000)),
        "end": str(int(END.timestamp() * 1000)),
    }
    assert [arguments for _, arguments in mcp.calls] == [total, {**total, "error": True}]
    assert result.summary == (
        "taxa de erro de authorizer-api entre 10:00 UTC e 11:00 UTC: 0,4% no início, 7,0% no "
        "fim, máximo 7,0% às 10:50 UTC. Série a cada 10 min: 0,4% · 0,5% · 0,4% · 3,3% · "
        "6,8% · 7,0%"
    )
    assert result.url == f"{BASE}/services/authorizer-api"


async def test_error_rate_is_zero_when_no_span_failed(mcp: FakeSignozMcp) -> None:
    def no_errors(arguments: dict[str, Any]) -> Any:
        if arguments.get("error"):
            return {"status": "success", "data": {"type": "time_series", "data": {"results": []}}}
        return fixture("aggregate_traces_count")

    mcp.answers["signoz_aggregate_traces"] = no_errors

    result = await mcp.telemetry().query_metric("authorizer-api", "taxa_de_erro", START, END)

    assert "0,0% no início, 0,0% no fim" in result.summary


async def test_latency_is_reported_in_milliseconds(mcp: FakeSignozMcp) -> None:
    mcp.answers["signoz_aggregate_traces"] = fixture("aggregate_traces_p99")

    result = await mcp.telemetry().query_metric("authorizer-api", "latencia_p99", START, END)

    assert mcp.calls[0][1]["aggregation"] == "p99"
    assert mcp.calls[0][1]["aggregateOn"] == "duration_nano"
    assert "182 ms no início, 2.398 ms no fim, máximo 2.402 ms às 10:40 UTC" in result.summary


async def test_throughput_is_calls_per_second(mcp: FakeSignozMcp) -> None:
    mcp.answers["signoz_aggregate_traces"] = fixture("aggregate_traces_rate")

    result = await mcp.telemetry().query_metric("authorizer-api", "vazao", START, END)

    assert mcp.calls[0][1]["aggregation"] == "rate"
    assert result.summary.startswith("vazão de authorizer-api")
    assert "20,1 chamadas/s no início" in result.summary


async def test_metric_without_data_says_so(mcp: FakeSignozMcp) -> None:
    mcp.answers["signoz_aggregate_traces"] = {"status": "success", "data": {"data": {}}}

    result = await mcp.telemetry().query_metric("authorizer-api", "vazao", START, END)

    assert result.summary == "sem dados de vazão de authorizer-api entre 10:00 UTC e 11:00 UTC"


async def test_logs_are_grouped_by_message(mcp: FakeSignozMcp) -> None:
    result = await mcp.telemetry().search_logs("authorizer-api", "timeout", START, END)

    assert mcp.calls[0][1]["filter"] == "severity_number >= 17"
    assert mcp.calls[0][1]["searchText"] == "timeout"
    assert result.summary == (
        '38 logs de erro de authorizer-api entre 10:00 UTC e 11:00 UTC com o texto "timeout". '
        'Mais frequentes: 31x "connection timeout to authorizer-db:5432 after 5000ms '
        '(pool exhausted)"; 7x "issuer gateway returned 503"'
    )
    assert result.url == f"{BASE}/logs/logs-explorer"


async def test_no_error_logs(mcp: FakeSignozMcp) -> None:
    mcp.answers["signoz_search_logs"] = fixture("search_empty")

    result = await mcp.telemetry().search_logs("authorizer-api", None, START, END)

    assert "searchText" not in mcp.calls[0][1]
    assert result.summary == "nenhum log de erro de authorizer-api entre 10:00 UTC e 11:00 UTC"


async def test_error_traces_link_to_a_trace(mcp: FakeSignozMcp) -> None:
    result = await mcp.telemetry().find_traces("authorizer-api", True, START, END)

    assert mcp.calls[0][1]["error"] is True
    assert result.summary == (
        "15 spans com erro de authorizer-api entre 10:00 UTC e 11:00 UTC: 12x POST /authorize "
        '("context deadline exceeded: acquire db connection"); 3x GET /health'
    )
    assert result.url == f"{BASE}/trace/a{0:031x}"


async def test_no_error_traces(mcp: FakeSignozMcp) -> None:
    mcp.answers["signoz_search_traces"] = fixture("search_empty")

    result = await mcp.telemetry().find_traces("authorizer-api", True, START, END)

    assert result.summary.startswith("nenhum span com erro")
    assert result.url == f"{BASE}/traces-explorer"


async def test_slow_traces_are_the_top_operations_by_p99(mcp: FakeSignozMcp) -> None:
    result = await mcp.telemetry().find_traces("authorizer-api", False, START, END)

    assert result.summary == (
        "operações mais lentas (p99) de authorizer-api entre 10:00 UTC e 11:00 UTC: "
        "POST /authorize 2.400 ms (7183 chamadas, 234 com erro); "
        "POST /capture 310 ms (1500 chamadas, 2 com erro); "
        "GET /status 90 ms (300 chamadas, 0 com erro)"
    )


@pytest.mark.parametrize("payload", [[], {"data": []}])
async def test_service_without_operations(mcp: FakeSignozMcp, payload: Any) -> None:
    mcp.answers["signoz_get_service_top_operations"] = payload

    result = await mcp.telemetry().find_traces("authorizer-api", False, START, END)

    assert result.summary.startswith("nenhuma operação com traces")


async def test_slow_server_is_unavailable(mcp: FakeSignozMcp) -> None:
    mcp.delay = 1.0

    with pytest.raises(ToolUnavailableError, match=r"sem resposta em 0\.05 s"):
        await mcp.telemetry(timeout_seconds=0.05).search_logs("authorizer-api", None, START, END)


async def test_unexpected_shape_is_unavailable(mcp: FakeSignozMcp) -> None:
    mcp.answers["signoz_search_logs"] = {"status": "success"}

    with pytest.raises(ToolUnavailableError, match="formato inesperado"):
        await mcp.telemetry().search_logs("authorizer-api", None, START, END)


async def test_non_json_answer_is_unavailable(mcp: FakeSignozMcp) -> None:
    mcp.answers["signoz_search_logs"] = "não é json"

    with pytest.raises(ToolUnavailableError, match="fora de JSON"):
        await mcp.telemetry().search_logs("authorizer-api", None, START, END)


async def test_unreachable_server_is_unavailable() -> None:
    def refuse(_request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("recusada")

    telemetry = SignozTelemetry(URL, BASE, 5.0, httpx2.MockTransport(refuse))

    with pytest.raises(ToolUnavailableError, match="signoz_search_logs"):
        await telemetry.search_logs("authorizer-api", None, START, END)

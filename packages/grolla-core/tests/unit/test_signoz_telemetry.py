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
from grolla.application.investigation import NO_DEPENDENCIES, Telemetry, ToolUnavailableError
from grolla.domain.incidents import UNKNOWN_SERVICE
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
    """Como o SigNoz real: o total sem filtro e, com error=true, só os spans com erro.

    Com `groupBy` é a agregação das dependências (escalar, por campo de destino).
    """
    if arguments.get("groupBy"):
        if arguments["aggregation"] == "p99":
            return fixture("dependencies_p99")
        return fixture("dependencies_errors" if arguments.get("error") else "dependencies_count")
    return fixture(
        "aggregate_traces_errors" if arguments.get("error") else "aggregate_traces_count"
    )


def default_answers() -> dict[str, Any]:
    return {
        "signoz_aggregate_traces": span_counts,
        "signoz_search_logs": fixture("search_logs"),
        "signoz_search_traces": fixture("search_traces_errors"),
        "signoz_get_service_top_operations": fixture("top_operations"),
        "signoz_list_alert_rules": fixture("list_alert_rules"),
        "signoz_get_alert": rule_definition,
        "signoz_list_services": fixture("list_services"),
    }


def rule_definition(arguments: dict[str, Any]) -> Any:
    """A definição real de uma regra, com o id e o nome da regra pedida."""
    listed = {r["ruleId"]: r for r in fixture("list_alert_rules")["data"]}
    answer = fixture("get_alert")
    rule = listed[arguments["id"]]
    answer["data"].update(id=rule["ruleId"], alert=rule["alert"], labels=rule["labels"])
    return answer


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

    def telemetry(
        self,
        timeout_seconds: float = 5.0,
        service_labels: tuple[str, ...] = ("service_name", "service.name"),
    ) -> SignozTelemetry:
        return SignozTelemetry(
            URL,
            BASE,
            timeout_seconds,
            httpx2.ASGITransport(app=self.app),
            service_labels=service_labels,
        )


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
        "stepInterval": "60",
        "start": str(int(START.timestamp() * 1000)),
        "end": str(int(END.timestamp() * 1000)),
    }
    assert [arguments for _, arguments in mcp.calls] == [total, {**total, "error": True}]
    assert result.summary == (
        "taxa de erro de authorizer-api entre 10:00 UTC e 11:00 UTC: 0,0% no início, 70,0% no "
        "fim, máximo 70,0% às 10:50 UTC. Mudou às 10:50 UTC, de 0,2% para 70,0%. Últimos 10 "
        "pontos, a cada 1 min: " + " · ".join(["70,0%"] * 10)
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
    assert "182 ms no início, 2.417 ms no fim, máximo 2.417 ms às 10:59 UTC" in result.summary
    assert "Mudou às 10:50 UTC, de 182 ms para 2.408 ms" in result.summary


async def test_throughput_is_calls_per_second(mcp: FakeSignozMcp) -> None:
    mcp.answers["signoz_aggregate_traces"] = fixture("aggregate_traces_rate")

    result = await mcp.telemetry().query_metric("authorizer-api", "vazao", START, END)

    assert mcp.calls[0][1]["aggregation"] == "rate"
    assert result.summary.startswith("vazão de authorizer-api")
    assert "20,0 chamadas/s no início" in result.summary
    assert "Sem mudança de patamar na janela" in result.summary
    assert result.summary.count("chamadas/s ·") == 9  # só os últimos 10 pontos


async def test_metric_without_data_says_so(mcp: FakeSignozMcp) -> None:
    mcp.answers["signoz_aggregate_traces"] = {"status": "success", "data": {"data": {}}}

    result = await mcp.telemetry().query_metric("authorizer-api", "vazao", START, END)

    assert result.summary == "sem dados de vazão de authorizer-api entre 10:00 UTC e 11:00 UTC"


async def test_logs_include_warn_grouped_by_message_with_the_level(mcp: FakeSignozMcp) -> None:
    result = await mcp.telemetry().search_logs("authorizer-api", "timeout", START, END)

    assert mcp.calls[0][1]["filter"] == "severity_number >= 13"
    assert mcp.calls[0][1]["searchText"] == "timeout"
    assert result.summary == (
        '60 logs WARN ou acima de authorizer-api entre 10:00 UTC e 11:00 UTC com o texto "timeout"'
        ". Mais frequentes: 31x [ERROR] connection timeout to authorizer-db:5432 after 5000ms "
        "(pool exhausted); "
        '22x [WARN] connection to server at "10.0.1.23", port 5432 failed: server closed the '
        "connection unexpectedly; "
        "7x [ERROR] issuer gateway returned 503"
    )
    assert result.url == f"{BASE}/logs/logs-explorer"


def log(body: str, level: str = "ERROR") -> dict[str, Any]:
    return {"data": {"body": body, "severity_text": level}}


async def test_variations_of_the_same_message_are_one_group_and_only_the_top_five(
    mcp: FakeSignozMcp,
) -> None:
    rows = [
        log("request 3f2504e0-4f89-11d3-9a0c-0305e82c3301 failed at 0x7ffd5e8c"),
        log("request 9b2c1d4e-0000-4aaa-8bbb-123456789abc failed at 0x10ab"),
        log("pid 123456 crashed", "FATAL"),
        log("pid 987654 crashed", "FATAL"),
        *(log(f"mensagem única {letter}", "warn") for letter in "abcde"),
    ]
    mcp.answers["signoz_search_logs"] = {"data": {"data": {"results": [{"rows": rows}]}}}

    result = await mcp.telemetry().search_logs("authorizer-api", None, START, END)

    top = result.summary.split("Mais frequentes: ")[1].split("; ")
    assert top[:2] == [
        "2x [ERROR] request 3f2504e0-4f89-11d3-9a0c-0305e82c3301 failed at 0x7ffd5e8c",
        "2x [FATAL] pid 123456 crashed",
    ]
    assert len(top) == 5
    assert top[2] == "1x [WARN] mensagem única a"


async def test_no_error_logs(mcp: FakeSignozMcp) -> None:
    mcp.answers["signoz_search_logs"] = fixture("search_empty")

    result = await mcp.telemetry().search_logs("authorizer-api", None, START, END)

    assert "searchText" not in mcp.calls[0][1]
    assert result.summary == (
        "nenhum log WARN ou acima de authorizer-api entre 10:00 UTC e 11:00 UTC"
    )


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


# ---- Memória: regras de alerta, serviços ativos e dependências


async def test_alert_rules_bring_the_readable_condition_and_the_service(
    mcp: FakeSignozMcp,
) -> None:
    rules = await mcp.telemetry().alert_rules()

    assert [name for name, _ in mcp.calls] == [
        "signoz_list_alert_rules",
        "signoz_get_alert",
        "signoz_get_alert",
    ]
    assert mcp.calls[0][1] == {"limit": "1000", "offset": "0"}
    pedidos = rules[1]
    assert pedidos.name == "Erros 5xx na pedidos-api"
    assert pedidos.service == "pedidos-api"
    assert pedidos.severity == "critical"
    assert pedidos.labels["team"] == "credenciadora"
    assert pedidos.condition == (
        "count() de spans com service.name = 'pedidos-api' AND has_error = true acima de 20 "
        "(pelo menos uma vez) na janela de 5m. Resumo: Spans com erro na pedidos-api acima de "
        "20 em 5 min"
    )
    assert pedidos.url == f"{BASE}/alerts/overview?ruleId=01a174f4-af14-7500-8aa9-e7907b82ff20"
    assert rules[0].service == "authorizer-api"


async def test_rule_without_service_label_and_disabled_rule(mcp: FakeSignozMcp) -> None:
    listing = fixture("list_alert_rules")
    listing["data"][0]["labels"] = {"severity": "warning", "time": "credenciadora"}
    listing["data"][1]["disabled"] = True
    mcp.answers["signoz_list_alert_rules"] = listing
    mcp.answers["signoz_get_alert"] = fixture("get_alert")

    rules = await mcp.telemetry(service_labels=("service",)).alert_rules()

    assert [r.name for r in rules] == ["Erros 5xx na pedidos-api"]  # o nome vem da definição
    assert rules[0].service == UNKNOWN_SERVICE


async def test_rule_listed_with_the_condition_skips_the_definition(mcp: FakeSignozMcp) -> None:
    definition = fixture("get_alert")["data"]
    definition["ruleId"] = definition["id"]
    definition["condition"] = {
        "compositeQuery": {"queries": [{"spec": {"name": "A", "query": "rate(x[5m]) > 1"}}]},
        "op": "2",
        "target": 1,
        "matchType": "3",
    }
    definition["evalWindow"] = "1h0m0s"
    definition["annotations"] = {}
    mcp.answers["signoz_list_alert_rules"] = {"data": [definition]}

    [rule] = await mcp.telemetry().alert_rules()

    assert [name for name, _ in mcp.calls] == ["signoz_list_alert_rules"]
    assert rule.condition == "rate(x[5m]) > 1 abaixo de 1 (na média) na janela de 1h"


async def test_unknown_condition_shape_is_said(mcp: FakeSignozMcp) -> None:
    answer = fixture("get_alert")
    answer["data"]["condition"] = {"compositeQuery": {"builderQueries": {}}}
    mcp.answers["signoz_get_alert"] = answer

    rules = await mcp.telemetry().alert_rules()

    assert rules[0].condition.startswith("condição em formato não reconhecido na janela de 5m")


async def test_listings_follow_the_pagination(mcp: FakeSignozMcp) -> None:
    first, second = fixture("list_services"), fixture("list_services")
    first["data"] = first["data"][:2]
    first["pagination"] = {"total": 3, "offset": 0, "hasMore": True, "nextOffset": 2}
    second["data"] = second["data"][2:]

    def pages(arguments: dict[str, Any]) -> Any:
        return first if arguments["offset"] == "0" else second

    mcp.answers["signoz_list_services"] = pages

    services = await mcp.telemetry().active_services(START)

    assert sorted(services) == ["authorizer-api", "captura-api", "pedidos-api"]
    assert [arguments["offset"] for _, arguments in mcp.calls] == ["0", "2"]
    assert mcp.calls[0][1]["start"] == str(int(START.timestamp() * 1000))


async def test_dependencies_are_the_outgoing_spans_with_calls_errors_and_p99(
    mcp: FakeSignozMcp,
) -> None:
    result = await mcp.telemetry().dependencies("pedidos-api", START, END)

    assert sorted(arguments["aggregation"] for _, arguments in mcp.calls) == [
        "count",
        "count",
        "p99",
    ]
    for _, arguments in mcp.calls:
        assert arguments["filter"] == "kind_string IN ('Client', 'Producer')"
        assert arguments["groupBy"] == "peer.service, db.system, db.name, server.address"
        assert arguments["requestType"] == "scalar"
    assert result.summary == (
        "2 dependência(s) vistas nos traces de pedidos-api entre 10:00 UTC e 11:00 UTC: "
        "banco postgresql pedidos: 79367 chamadas, 0,0% com erro, p99 5.002 ms; "
        "host api.bandeira.example.com: 1520 chamadas, 2,5% com erro, p99 820 ms"
    )
    assert result.url == f"{BASE}/traces-explorer"


async def test_dependency_names_say_the_kind_and_the_address(mcp: FakeSignozMcp) -> None:
    answer = fixture("dependencies_count")
    answer["data"]["data"]["results"][0]["data"] = [
        ["pagamentos-api", None, None, "10.0.0.1", 30],
        ["grolla-lab-db", "postgresql", "", None, 20],
        [None, None, None, None, 10],
        ["sem-chamadas", None, None, None, 0],
    ]
    mcp.answers["signoz_aggregate_traces"] = answer

    result = await mcp.telemetry().dependencies("pedidos-api", START, END)

    assert "3 dependência(s)" in result.summary
    assert "serviço pagamentos-api (10.0.0.1): 30 chamadas" in result.summary
    assert "banco postgresql (grolla-lab-db): 20 chamadas" in result.summary
    assert "destino sem identificação: 10 chamadas" in result.summary
    assert "sem-chamadas" not in result.summary


async def test_service_without_outgoing_spans_has_no_dependencies(mcp: FakeSignozMcp) -> None:
    mcp.answers["signoz_aggregate_traces"] = fixture("dependencies_empty")

    result = await mcp.telemetry().dependencies("captura-api", START, END)

    assert result.summary.startswith(NO_DEPENDENCIES)

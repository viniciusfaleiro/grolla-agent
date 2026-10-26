"""MCP fake do SigNoz: cliente MCP de verdade contra o app dos fakes, por ASGI, sem rede."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
from fastapi import FastAPI
from fastapi.testclient import TestClient
from grolla_fakes.app import create_app
from grolla_fakes.signoz_mcp import TOOLS
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

URL = "http://fakes:9000/signoz/mcp"


def window(minutes: int = 60) -> dict[str, str]:
    end = datetime.now(UTC)
    start = end - timedelta(minutes=minutes)
    return {"start": str(int(start.timestamp() * 1000)), "end": str(int(end.timestamp() * 1000))}


async def call(app: FastAPI, tool: str, arguments: dict[str, Any]) -> CallToolResult:
    async with (
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)) as http,
        Client(streamable_http_client(URL, http_client=http), mode="legacy", cache=None) as client,
    ):
        return await client.call_tool(tool, arguments)


def payload(result: CallToolResult) -> Any:
    content = result.content[0]
    assert isinstance(content, TextContent)
    return json.loads(content.text)


async def test_lists_only_the_read_tools() -> None:
    async with (
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=create_app())) as http,
        Client(streamable_http_client(URL, http_client=http), mode="legacy", cache=None) as client,
    ):
        tools = await client.list_tools()

    assert {t.name for t in tools.tools} == set(TOOLS)


async def test_default_error_rate_rises_after_the_deploy() -> None:
    app = create_app()
    base = {
        "aggregation": "count",
        "service": "authorizer-api",
        "requestType": "time_series",
        "stepInterval": "600",
        **window(),
    }
    total = await call(app, "signoz_aggregate_traces", base)
    failed = await call(app, "signoz_aggregate_traces", {**base, "error": True})

    def values(result: Any) -> list[float]:
        series = payload(result)["data"]["data"]["results"][0]["aggregations"][0]["series"]
        return [point["value"] for point in series[0]["values"]]

    rate = [e / t for e, t in zip(values(failed), values(total), strict=True)]
    assert rate[0] < rate[-1]


async def test_default_latency_and_throughput() -> None:
    app = create_app()
    p99 = await call(app, "signoz_aggregate_traces", {"aggregation": "p99", **window()})
    rate = await call(app, "signoz_aggregate_traces", {"aggregation": "rate", **window()})

    p99_values = payload(p99)["data"]["data"]["results"][0]["aggregations"][0]["series"][0]
    assert p99_values["values"][-1]["value"] > p99_values["values"][0]["value"]
    rate_values = payload(rate)["data"]["data"]["results"][0]["aggregations"][0]["series"][0]
    assert {v["value"] for v in rate_values["values"]} == {20.0}


async def test_default_logs_show_the_database_timeout_and_the_dropped_connection() -> None:
    app = create_app()
    found = await call(app, "signoz_search_logs", {"service": "authorizer-api", **window()})
    filtered = await call(
        app, "signoz_search_logs", {"service": "authorizer-api", "searchText": "503", **window()}
    )

    rows = payload(found)["data"]["data"]["results"][0]["rows"]
    levels = [row["data"]["severity_text"] for row in rows]
    assert (levels.count("ERROR"), levels.count("WARN")) == (31, 22)
    bodies = {row["data"]["body"] for row in rows}
    assert any("connection timeout to authorizer-api-db" in body for body in bodies)
    assert any("server closed the connection unexpectedly" in body for body in bodies)
    assert payload(filtered)["data"]["data"]["results"][0]["rows"] == []


async def test_default_traces_and_top_operations() -> None:
    app = create_app()
    traces = await call(app, "signoz_search_traces", {"service": "authorizer-api", **window()})
    operations = await call(app, "signoz_get_service_top_operations", {"service": "x"})

    rows = payload(traces)["data"]["data"]["results"][0]["rows"]
    assert len(rows) == 12
    assert rows[0]["data"]["has_error"] is True
    assert payload(operations)[0]["name"] == "POST /authorize"


async def test_old_window_has_no_errors() -> None:
    end = datetime.now(UTC) - timedelta(hours=2)
    start = end - timedelta(hours=1)
    old = {"start": str(int(start.timestamp() * 1000)), "end": str(int(end.timestamp() * 1000))}

    result = await call(create_app(), "signoz_search_logs", {"service": "a", **old})

    assert payload(result)["data"]["data"]["results"][0]["rows"] == []


async def test_script_fixes_answers_and_failures_and_reset_clears() -> None:
    app = create_app()
    control = TestClient(app)
    control.post(
        "/_control/signoz",
        json={
            "responses": {"signoz_search_logs": {"fixo": 1}},
            "failing": ["signoz_search_traces"],
        },
    )

    assert payload(await call(app, "signoz_search_logs", {})) == {"fixo": 1}
    assert (await call(app, "signoz_search_traces", {})).is_error

    control.post("/_control/reset")
    assert not (await call(app, "signoz_search_traces", window())).is_error


async def test_calls_are_recorded_and_unknown_tools_fail() -> None:
    app = create_app()

    result = await call(app, "signoz_create_dashboard", {"title": "x"})

    assert result.is_error
    recorded = TestClient(app).get("/_control/requests").json()
    assert recorded == [
        {
            "method": "POST",
            "path": "/signoz/mcp",
            "body": {"tool": "signoz_create_dashboard", "arguments": {"title": "x"}},
            "status": 500,
        }
    ]


async def test_default_alert_rule_is_of_the_team_with_its_condition() -> None:
    app = create_app()

    listing = payload(await call(app, "signoz_list_alert_rules", {"limit": "1000"}))
    [rule] = listing["data"]
    definition = payload(await call(app, "signoz_get_alert", {"id": rule["ruleId"]}))["data"]

    assert rule["alert"] == "Taxa de erro alta no estudo-api"
    assert rule["labels"]["service_name"] == "estudo-api"
    assert rule["labels"]["team"] == "credenciadora"
    assert listing["pagination"]["hasMore"] is False
    assert definition["id"] == rule["ruleId"]
    assert definition["condition"]["target"] == 20
    assert definition["evalWindow"] == "5m0s"


async def test_active_services_include_the_studied_one() -> None:
    listing = payload(await call(create_app(), "signoz_list_services", window()))

    assert "estudo-api" in [s["serviceName"] for s in listing["data"]]


async def test_dependencies_are_the_service_database() -> None:
    app = create_app()
    base = {
        "service": "estudo-api",
        "groupBy": "peer.service, db.system, db.name, server.address",
        "requestType": "scalar",
        **window(),
    }

    calls = payload(await call(app, "signoz_aggregate_traces", {**base, "aggregation": "count"}))
    failed = payload(
        await call(app, "signoz_aggregate_traces", {**base, "aggregation": "count", "error": True})
    )

    [result] = calls["data"]["data"]["results"]
    assert [c["name"] for c in result["columns"]][:4] == [
        "peer.service",
        "db.system",
        "db.name",
        "server.address",
    ]
    assert result["data"] == [["estudo-db", "postgresql", "estudo", None, 4200]]
    assert failed["data"]["data"]["results"][0]["data"][0][-1] == 12

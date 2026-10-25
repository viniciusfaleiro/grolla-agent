"""Toolbox, orçamento de tokens e prompt da investigação."""

from datetime import timedelta
from typing import Any

import pytest
from grolla.application.investigation import (
    NO_REPOSITORY,
    SYSTEM_PROMPT,
    UNAVAILABLE,
    BudgetExceededError,
    InvestigationBrief,
    TokenBudget,
    Toolbox,
    brief_text,
)
from grolla.domain.analysis import Evaluation
from grolla.testing.builders import T0, make_alert, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.investigation import (
    FAKE_SOURCE_URL,
    FakeChangeHistory,
    FakeTelemetry,
    fake_usage,
)

SERVICE = "authorizer-api"
LOOKBACK = timedelta(minutes=60)


def toolbox(
    telemetry: FakeTelemetry | None = None, changes: FakeChangeHistory | None = None
) -> Toolbox:
    return Toolbox(
        telemetry or FakeTelemetry(),
        changes or FakeChangeHistory({SERVICE: "1 deploy: 2.4.1 às 09:58 por ana"}),
        FakeClock(T0),
        LOOKBACK,
    )


def test_specs_are_the_four_neutral_tools_with_closed_arguments() -> None:
    specs = {spec.name: spec.input_schema for spec in toolbox().specs()}

    assert set(specs) == {"query_metric", "search_logs", "find_traces", "recent_deploys"}
    assert {name: set(schema["properties"]) for name, schema in specs.items()} == {
        "query_metric": {"service", "signal"},
        "search_logs": {"service", "text"},
        "find_traces": {"service", "only_errors"},
        "recent_deploys": {"service"},
    }
    assert {name: schema["required"] for name, schema in specs.items()} == {
        "query_metric": ["service", "signal"],
        "search_logs": ["service"],
        "find_traces": ["service", "only_errors"],
        "recent_deploys": ["service"],
    }
    assert all(schema["additionalProperties"] is False for schema in specs.values())


async def test_query_metric_uses_the_lookback_window_and_records_the_evaluation() -> None:
    telemetry = FakeTelemetry()
    tools = toolbox(telemetry)

    answer = await tools.call("query_metric", {"service": SERVICE, "signal": "latencia_p99"})

    assert answer == f"latencia_p99 de {SERVICE} estável na janela\nLink: {FAKE_SOURCE_URL}"
    assert telemetry.calls == [
        (
            "query_metric",
            {"service": SERVICE, "signal": "latencia_p99", "start": T0 - LOOKBACK, "end": T0},
        )
    ]
    assert tools.evaluations == (
        Evaluation(
            "query_metric",
            f"latencia_p99 de {SERVICE}, últimos 60 min",
            f"latencia_p99 de {SERVICE} estável na janela",
            FAKE_SOURCE_URL,
            ok=True,
        ),
    )


@pytest.mark.parametrize(
    ("name", "arguments", "subject", "call"),
    [
        (
            "search_logs",
            {"service": SERVICE, "text": "timeout"},
            f"logs de erro de {SERVICE} com 'timeout', últimos 60 min",
            ("search_logs", {"service": SERVICE, "text": "timeout"}),
        ),
        (
            "search_logs",
            {"service": SERVICE, "text": ""},
            f"logs de erro de {SERVICE}, últimos 60 min",
            ("search_logs", {"service": SERVICE, "text": None}),
        ),
        (
            "find_traces",
            {"service": SERVICE, "only_errors": True},
            f"traces com erro de {SERVICE}, últimos 60 min",
            ("find_traces", {"service": SERVICE, "only_errors": True}),
        ),
        (
            "find_traces",
            {"service": SERVICE, "only_errors": False},
            f"traces lentos de {SERVICE}, últimos 60 min",
            ("find_traces", {"service": SERVICE, "only_errors": False}),
        ),
    ],
)
async def test_telemetry_tools_describe_what_was_evaluated(
    name: str, arguments: dict[str, Any], subject: str, call: tuple[str, dict[str, Any]]
) -> None:
    telemetry = FakeTelemetry()
    tools = toolbox(telemetry)

    await tools.call(name, arguments)

    assert tools.evaluations[0].subject == subject
    assert telemetry.calls == [(call[0], {**call[1], "start": T0 - LOOKBACK, "end": T0})]


async def test_recent_deploys_since_the_lookback_and_without_link_has_no_link_line() -> None:
    changes = FakeChangeHistory()
    tools = toolbox(changes=changes)

    answer = await tools.call("recent_deploys", {"service": "sem-repo"})

    assert answer == NO_REPOSITORY
    assert changes.calls == [("sem-repo", T0 - LOOKBACK)]
    assert tools.evaluations == (
        Evaluation(
            "recent_deploys", "deploys de sem-repo, últimos 60 min", NO_REPOSITORY, None, ok=True
        ),
    )


async def test_tool_failure_becomes_an_unavailable_evaluation_and_never_raises() -> None:
    tools = toolbox(FakeTelemetry(failing={"search_logs"}))

    answer = await tools.call("search_logs", {"service": SERVICE})

    assert answer.startswith(UNAVAILABLE)
    assert tools.evaluations == (
        Evaluation(
            "search_logs", f"logs de erro de {SERVICE}, últimos 60 min", UNAVAILABLE, None, ok=False
        ),
    )


async def test_unexpected_error_of_a_tool_also_becomes_an_evaluation() -> None:
    tools = toolbox(FakeTelemetry(failing={"find_traces"}, error=TimeoutError))

    answer = await tools.call("find_traces", {"service": SERVICE, "only_errors": True})

    assert answer == f"{UNAVAILABLE}: find_traces fora do ar no fake"
    assert not tools.evaluations[0].ok


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("delete_dashboard", {"service": SERVICE}),
        ("query_metric", {"service": SERVICE}),
        ("query_metric", {"service": SERVICE, "signal": "cpu"}),
        ("query_metric", {"signal": "vazao"}),
        ("search_logs", {"service": ""}),
        ("search_logs", {"service": SERVICE, "text": 3}),
        ("find_traces", {"service": SERVICE, "only_errors": "sim"}),
        ("find_traces", {"service": SERVICE}),
    ],
)
async def test_invalid_call_is_answered_and_recorded_without_calling_the_source(
    name: str, arguments: dict[str, Any]
) -> None:
    telemetry = FakeTelemetry()
    tools = toolbox(telemetry)

    answer = await tools.call(name, arguments)

    assert answer.startswith(f"{UNAVAILABLE}: argumentos inválidos")
    assert telemetry.calls == []
    assert tools.evaluations == (
        Evaluation(name, f"chamada inválida de {name}", UNAVAILABLE, None, ok=False),
    )


def test_budget_counts_every_token_and_raises_when_the_limit_is_reached() -> None:
    budget = TokenBudget(limit=2_300)
    cached = fake_usage(100)
    cached = type(cached)(cached.stage, cached.model, 100, 10, 50, 40)

    budget.spend(fake_usage(1_000))
    budget.spend(cached)
    assert budget.total == 1_100 + 200

    with pytest.raises(BudgetExceededError):
        budget.spend(fake_usage(1_000))
    assert budget.usages == (fake_usage(1_000), cached, fake_usage(1_000))


def test_brief_text_has_the_incident_the_alerts_and_the_services_line() -> None:
    incident = make_incident(services=(SERVICE, "capture-api"))
    alert = make_alert(service=SERVICE, labels={"service_name": SERVICE, "env": "prod"})

    text = brief_text(InvestigationBrief(incident, (alert,), now=T0 + timedelta(minutes=3)))

    assert f"Serviços: {SERVICE}, capture-api\n" in text
    assert "Severidade: P3" in text
    assert alert.description in text
    assert "env=prod, service_name=authorizer-api" in text
    assert "2026-01-01T10:03:00+00:00" in text


def test_brief_text_without_alerts_says_so() -> None:
    text = brief_text(InvestigationBrief(make_incident(), (), now=T0))

    assert "- nenhum alerta registrado" in text


def test_system_prompt_follows_the_short_script() -> None:
    for expected in (
        "query_metric",
        "recent_deploys",
        "find_traces",
        "search_logs",
        "confidence",
        "evidence",
        "plan",
        "somente leitura",
        "não há causa clara",
    ):
        assert expected in SYSTEM_PROMPT

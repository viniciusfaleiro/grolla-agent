"""Toolbox, orçamento de tokens e prompt da investigação."""

from datetime import timedelta
from typing import Any

import pytest
from grolla.application.investigation import (
    CHAT_PROMPT,
    CONSOLE_PROMPT,
    HEALTH_UNAVAILABLE,
    NO_CLOUD,
    NO_CLOUD_RESOURCES,
    NO_CLUSTER,
    NO_CLUSTER_CHANGES,
    NO_REPOSITORY,
    NO_WORKLOADS,
    SYSTEM_PROMPT,
    UNAVAILABLE,
    VERIFICATION_PROMPT,
    BudgetExceededError,
    CloudInventory,
    ClusterInventory,
    InvestigationBrief,
    TokenBudget,
    Toolbox,
    brief_text,
    verification_text,
)
from grolla.domain.analysis import AnalysisStatus, Evaluation
from grolla.testing.builders import T0, make_alert, make_analysis, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.investigation import (
    CLOUD_TOOLS,
    FAKE_SOURCE_URL,
    FakeChangeHistory,
    FakeCloudInventory,
    FakeClusterInventory,
    FakeTelemetry,
    fake_usage,
)

SERVICE = "authorizer-api"
LOOKBACK = timedelta(minutes=60)


def toolbox(
    telemetry: FakeTelemetry | None = None,
    changes: FakeChangeHistory | None = None,
    cloud: CloudInventory | None = None,
    cluster: ClusterInventory | None = None,
) -> Toolbox:
    return Toolbox(
        telemetry or FakeTelemetry(),
        changes or FakeChangeHistory({SERVICE: "1 deploy: 2.4.1 às 09:58 por ana"}),
        FakeClock(T0),
        LOOKBACK,
        cloud,
        cluster=cluster,
    )


def test_specs_are_the_neutral_tools_with_closed_arguments() -> None:
    specs = {spec.name: spec.input_schema for spec in toolbox().specs()}

    assert {name: set(schema["properties"]) for name, schema in specs.items()} == {
        "query_metric": {"service", "signal"},
        "search_logs": {"service", "text"},
        "find_traces": {"service", "only_errors"},
        "recent_deploys": {"service"},
        "cloud_resources": {"service"},
        "cloud_changes": {"service"},
        "cloud_alarms": {"service"},
        "cloud_health": {"service"},
        "cloud_quotas": {"service"},
        "cluster_state": {"service"},
        "cluster_changes": {"service"},
        "read_file": {"service", "path"},
        "service_dependencies": {"service"},
        "cloud_overview": set(),
    }
    assert {name: schema["required"] for name, schema in specs.items()} == {
        "query_metric": ["service", "signal"],
        "search_logs": ["service"],
        "find_traces": ["service", "only_errors"],
        "recent_deploys": ["service"],
        "cloud_resources": ["service"],
        "cloud_changes": ["service"],
        "cloud_alarms": ["service"],
        "cloud_health": ["service"],
        "cloud_quotas": ["service"],
        "cluster_state": ["service"],
        "cluster_changes": ["service"],
        "read_file": ["service", "path"],
        "service_dependencies": ["service"],
        "cloud_overview": [],
    }
    descriptions = " ".join(spec.description for spec in toolbox().specs())
    assert "nuvem" in descriptions
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


def test_system_prompt_uses_the_cloud_and_relates_rollback_to_the_deploy_time() -> None:
    for expected in ("cloud_resources", "cloud_changes", "banco, fila, rede", "rollback"):
        assert expected in SYSTEM_PROMPT
    assert "começou depois do deploy" in SYSTEM_PROMPT


def test_prompts_say_when_to_look_at_provider_events_and_quotas() -> None:
    for prompt in (SYSTEM_PROMPT, VERIFICATION_PROMPT):
        for expected in ("`cloud_health`", "`cloud_quotas`", "região", "throttling", "escala"):
            assert expected in prompt
    for prompt in (CHAT_PROMPT, CONSOLE_PROMPT):
        assert "`cloud_health`, `cloud_quotas`" in prompt


def test_prompts_say_when_to_look_at_the_cluster() -> None:
    for prompt in (SYSTEM_PROMPT, VERIFICATION_PROMPT, CHAT_PROMPT, CONSOLE_PROMPT):
        for expected in (
            "`cluster_state`",
            "`cluster_changes`",
            "reinício ou lentidão",
            "sem deploy no pipeline",
            "o horário e a ferramenta",
        ):
            assert expected in prompt
    for prompt in (SYSTEM_PROMPT, VERIFICATION_PROMPT):
        assert "reverter" in prompt
        assert "pode ser a causa" in prompt


async def test_health_without_the_plan_is_an_answer() -> None:
    cloud = FakeCloudInventory(health={SERVICE: HEALTH_UNAVAILABLE})
    tools = toolbox(cloud=cloud)

    answer = await tools.call("cloud_health", {"service": SERVICE})

    assert answer.startswith(HEALTH_UNAVAILABLE)
    assert tools.evaluations[0].ok


def test_verification_prompt_asks_verdicts_root_cause_and_a_concrete_plan() -> None:
    for expected in (
        "confirmada, rejeitada ou inconclusiva",
        "root_cause",
        "actions",
        "missing",
        "o quê, quando e onde",
        "fazer rollback para o build X",
        "religar o recurso Y",
        "escalar Z",
        "Nunca coloque passos de investigação",
        "somente leitura",
        "cloud_changes",
    ):
        assert expected in VERIFICATION_PROMPT


def test_verification_text_has_the_services_line_hypotheses_and_evaluations() -> None:
    incident = make_incident(services=(SERVICE, "capture-api"))
    analysis = make_analysis(incident.id)

    text = verification_text(InvestigationBrief(incident, (), now=T0), analysis)

    assert f"Serviços: {SERVICE}, capture-api\n" in text
    assert f"Resumo da análise inicial: {analysis.summary}" in text
    assert "- Hipótese 1 (confiança 80%)" in text
    assert "  Evidência: Evidência da hipótese 1 (https://origem.interno/1)" in text
    assert "  Evidência: Sem link\n" in text
    assert "- query_metric: taxa_de_erro de capture-api, últimos 60 min → Subiu" in text
    assert "- search_logs: logs de erro de capture-api, últimos 60 min → não foi" in text


def test_verification_text_without_evaluations_says_so() -> None:
    incident = make_incident()
    analysis = make_analysis(incident.id, status=AnalysisStatus.PENDING)

    text = verification_text(InvestigationBrief(incident, (), now=T0), analysis)

    assert "- nenhuma avaliação registrada" in text


@pytest.mark.parametrize(
    ("name", "subject", "call"),
    [
        ("cloud_resources", f"recursos de nuvem de {SERVICE}", "resources"),
        ("cloud_changes", f"mudanças na nuvem de {SERVICE}, últimos 60 min", "changes"),
        ("cloud_alarms", f"alarmes da nuvem de {SERVICE}", "alarms"),
        ("cloud_health", f"eventos do provedor de nuvem para {SERVICE}", "health"),
        ("cloud_quotas", f"cotas da nuvem de {SERVICE}", "quotas"),
    ],
)
async def test_cloud_tools_call_the_cloud_with_the_service(
    name: str, subject: str, call: str
) -> None:
    cloud = FakeCloudInventory(
        resources={SERVICE: "banco pedidos-db: stopped"},
        changes={SERVICE: "21:42 parada do banco"},
        alarms={SERVICE: "alarme de conexões"},
        health={SERVICE: "evento aberto: banco de dados em sa-east-1"},
        quotas={SERVICE: "instâncias de banco: 18 de 20 (90%)"},
    )
    tools = toolbox(cloud=cloud)

    answer = await tools.call(name, {"service": SERVICE})

    assert answer.endswith(f"Link: https://console.nuvem.interno/{SERVICE}")
    assert cloud.calls == [(call, SERVICE)]
    assert tools.evaluations[0].subject == subject
    assert tools.evaluations[0].ok


async def test_cloud_changes_since_the_lookback() -> None:
    cloud = FakeCloudInventory()

    await toolbox(cloud=cloud).call("cloud_changes", {"service": SERVICE})

    assert cloud.since == [T0 - LOOKBACK]


async def test_service_without_cloud_resources_is_an_answer() -> None:
    tools = toolbox(cloud=FakeCloudInventory())

    assert await tools.call("cloud_resources", {"service": SERVICE}) == NO_CLOUD_RESOURCES
    assert tools.evaluations[0].ok


@pytest.mark.parametrize(
    "name", ["cloud_resources", "cloud_changes", "cloud_alarms", "cloud_health", "cloud_quotas"]
)
async def test_without_cloud_the_tools_say_it_is_not_configured(name: str) -> None:
    tools = toolbox()

    assert await tools.call(name, {"service": SERVICE}) == NO_CLOUD
    assert tools.evaluations[0].ok


async def test_cloud_failure_becomes_an_unavailable_evaluation() -> None:
    tools = toolbox(cloud=FakeCloudInventory(failing=CLOUD_TOOLS))

    answer = await tools.call("cloud_changes", {"service": SERVICE})

    assert answer.startswith(UNAVAILABLE)
    assert tools.evaluations[0].finding == UNAVAILABLE
    assert not tools.evaluations[0].ok


@pytest.mark.parametrize(
    ("name", "subject", "call", "summary"),
    [
        ("cluster_state", f"estado no cluster de {SERVICE}", "state", "pod OOMKilled"),
        ("cluster_changes", f"mudanças no cluster de {SERVICE}", "changes", "03:10 ConfigMap"),
    ],
)
async def test_cluster_tools_call_the_cluster_since_the_lookback(
    name: str, subject: str, call: str, summary: str
) -> None:
    cluster = FakeClusterInventory(
        state={SERVICE: "pod OOMKilled"}, changes={SERVICE: "03:10 ConfigMap"}
    )
    tools = toolbox(cluster=cluster)

    answer = await tools.call(name, {"service": SERVICE})

    assert answer == summary
    assert cluster.calls == [(call, SERVICE)]
    assert cluster.since == [T0 - LOOKBACK]
    assert tools.evaluations[0].subject == subject
    assert tools.evaluations[0].ok


async def test_service_outside_the_cluster_is_an_answer() -> None:
    tools = toolbox(cluster=FakeClusterInventory())

    assert await tools.call("cluster_state", {"service": SERVICE}) == NO_WORKLOADS
    assert await tools.call("cluster_changes", {"service": SERVICE}) == NO_CLUSTER_CHANGES
    assert all(evaluation.ok for evaluation in tools.evaluations)


@pytest.mark.parametrize("name", ["cluster_state", "cluster_changes"])
async def test_without_cluster_the_tools_say_it_is_not_configured(name: str) -> None:
    tools = toolbox()

    assert await tools.call(name, {"service": SERVICE}) == NO_CLUSTER
    assert tools.evaluations[0].ok


async def test_cluster_failure_becomes_an_unavailable_evaluation() -> None:
    tools = toolbox(cluster=FakeClusterInventory(failing={"state", "changes"}))

    answer = await tools.call("cluster_state", {"service": SERVICE})

    assert answer.startswith(UNAVAILABLE)
    assert not tools.evaluations[0].ok

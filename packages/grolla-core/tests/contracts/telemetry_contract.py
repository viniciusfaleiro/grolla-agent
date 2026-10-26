"""Suíte de contrato de Telemetry.

O adapter sobrescreve:
- `telemetry`: o adapter configurado com a fonte respondendo (transporte fake com sucesso);
- `unavailable_telemetry`: o mesmo adapter com a fonte fora do ar ou estourando o timeout;
- `service`, se o cenário de sucesso só conhece um serviço específico.
"""

from datetime import timedelta

import pytest
from grolla.application.investigation import (
    NO_DEPENDENCIES,
    SIGNALS,
    AlertRule,
    Signal,
    Telemetry,
    ToolResult,
    ToolUnavailableError,
)
from grolla.testing.builders import T0

START = T0 - timedelta(hours=1)


def assert_compact(result: ToolResult) -> None:
    assert isinstance(result, ToolResult)
    assert result.summary.strip()
    assert result.url is None or result.url.startswith("http")


class TelemetryContract:
    @pytest.fixture
    def telemetry(self) -> Telemetry:
        raise NotImplementedError

    @pytest.fixture
    def unavailable_telemetry(self) -> Telemetry:
        raise NotImplementedError

    @pytest.fixture
    def service(self) -> str:
        return "authorizer-api"

    @pytest.mark.parametrize("signal", SIGNALS)
    async def test_query_metric_returns_a_compact_summary_for_every_signal(
        self, telemetry: Telemetry, service: str, signal: Signal
    ) -> None:
        assert_compact(await telemetry.query_metric(service, signal, START, T0))

    @pytest.mark.parametrize("text", [None, "timeout"])
    async def test_search_logs_returns_a_compact_summary(
        self, telemetry: Telemetry, service: str, text: str | None
    ) -> None:
        assert_compact(await telemetry.search_logs(service, text, START, T0))

    @pytest.mark.parametrize("only_errors", [True, False])
    async def test_find_traces_returns_a_compact_summary(
        self, telemetry: Telemetry, service: str, only_errors: bool
    ) -> None:
        assert_compact(await telemetry.find_traces(service, only_errors, START, T0))

    async def test_unavailable_source_raises_tool_unavailable(
        self, unavailable_telemetry: Telemetry, service: str
    ) -> None:
        with pytest.raises(ToolUnavailableError):
            await unavailable_telemetry.query_metric(service, "taxa_de_erro", START, T0)
        with pytest.raises(ToolUnavailableError):
            await unavailable_telemetry.search_logs(service, None, START, T0)
        with pytest.raises(ToolUnavailableError):
            await unavailable_telemetry.find_traces(service, True, START, T0)


class EnvironmentTelemetryContract:
    """As leituras do estudo do ambiente: regras de alerta, serviços ativos e dependências.

    Fica fora de `TelemetryContract` para o adapter ganhar a suíte quando implementar. O
    adapter sobrescreve `telemetry` (a fonte conhece ao menos uma regra de alerta e o `service`
    ativo, com dependências), `quiet_service` (sem spans de saída na janela) e
    `unavailable_telemetry`.
    """

    @pytest.fixture
    def telemetry(self) -> Telemetry:
        raise NotImplementedError

    @pytest.fixture
    def unavailable_telemetry(self) -> Telemetry:
        raise NotImplementedError

    @pytest.fixture
    def service(self) -> str:
        return "pedidos-api"

    @pytest.fixture
    def quiet_service(self) -> str:
        return "servico-sem-traces"

    async def test_alert_rules_are_readable(self, telemetry: Telemetry) -> None:
        rules = await telemetry.alert_rules()

        assert rules
        for rule in rules:
            assert isinstance(rule, AlertRule)
            assert rule.name.strip()
            assert rule.service.strip()
            assert rule.url is None or rule.url.startswith("http")

    async def test_active_services_include_the_service(
        self, telemetry: Telemetry, service: str
    ) -> None:
        assert service in await telemetry.active_services(START)

    async def test_dependencies_return_a_compact_summary(
        self, telemetry: Telemetry, service: str
    ) -> None:
        result = await telemetry.dependencies(service, START, T0)

        assert_compact(result)
        assert NO_DEPENDENCIES not in result.summary

    async def test_service_without_outgoing_spans_is_an_answer_not_an_error(
        self, telemetry: Telemetry, quiet_service: str
    ) -> None:
        result = await telemetry.dependencies(quiet_service, START, T0)

        assert NO_DEPENDENCIES in result.summary

    async def test_unavailable_source_raises_tool_unavailable(
        self, unavailable_telemetry: Telemetry, service: str
    ) -> None:
        with pytest.raises(ToolUnavailableError):
            await unavailable_telemetry.alert_rules()
        with pytest.raises(ToolUnavailableError):
            await unavailable_telemetry.active_services(START)
        with pytest.raises(ToolUnavailableError):
            await unavailable_telemetry.dependencies(service, START, T0)

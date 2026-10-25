"""Suíte de contrato de Telemetry.

O adapter sobrescreve:
- `telemetry`: o adapter configurado com a fonte respondendo (transporte fake com sucesso);
- `unavailable_telemetry`: o mesmo adapter com a fonte fora do ar ou estourando o timeout;
- `service`, se o cenário de sucesso só conhece um serviço específico.
"""

from datetime import timedelta

import pytest
from grolla.application.investigation import (
    SIGNALS,
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

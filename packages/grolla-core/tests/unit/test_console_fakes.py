"""Os fakes do console passam nas suítes de contrato: a loja em memória, a série da telemetria
e a resposta do modelo roteirizado.
"""

from datetime import timedelta

import pytest
from contracts.console_store_contract import ConsoleStoreContract
from contracts.language_model_contract import SERVICE, ConsoleModelContract
from contracts.telemetry_contract import TelemetrySeriesContract
from grolla.application.console import ConsoleStore
from grolla.application.investigation import (
    LanguageModel,
    LanguageModelError,
    Telemetry,
    TokenBudget,
    Toolbox,
)
from grolla.domain.console import ChartRequest, ConsoleDraft
from grolla.testing.builders import T0
from grolla.testing.clock import FakeClock
from grolla.testing.console import InMemoryConsoleStore
from grolla.testing.investigation import (
    SERIES_POINTS,
    FakeChangeHistory,
    FakeTelemetry,
    ScriptedLanguageModel,
)

CALLS = [("query_metric", {"service": SERVICE, "signal": "taxa_de_erro"})]


class TestInMemoryConsoleStoreContract(ConsoleStoreContract):
    @pytest.fixture
    def store(self) -> ConsoleStore:
        return InMemoryConsoleStore()


class TestFakeTelemetrySeriesContract(TelemetrySeriesContract):
    @pytest.fixture
    def telemetry(self) -> Telemetry:
        return FakeTelemetry()

    @pytest.fixture
    def unavailable_telemetry(self) -> Telemetry:
        return FakeTelemetry(failing={"series"})


class TestScriptedLanguageModelConsoleContract(ConsoleModelContract):
    @pytest.fixture
    def consulting_model(self) -> LanguageModel:
        return ScriptedLanguageModel(console_calls=CALLS)

    @pytest.fixture
    def failing_consultant(self) -> LanguageModel:
        return ScriptedLanguageModel(console_error=LanguageModelError("API fora do ar"))


async def test_default_series_has_evenly_spaced_points_in_the_window() -> None:
    start = T0 - timedelta(minutes=55)

    points = await FakeTelemetry().series(SERVICE, "latencia_p99", start, T0)

    assert len(points) == SERIES_POINTS
    assert (points[0][0], points[-1][0]) == (start, T0)
    assert {value for _, value in points} == {180.0}


async def test_scripted_series_keeps_only_the_window() -> None:
    inside, outside = (T0 - timedelta(minutes=1), 3.0), (T0 - timedelta(hours=2), 9.0)
    telemetry = FakeTelemetry(points={(SERVICE, "vazao"): [inside, outside]})

    points = await telemetry.series(SERVICE, "vazao", T0 - timedelta(hours=1), T0)

    assert points == [inside]
    assert telemetry.calls[0][0] == "series"


def _toolbox() -> Toolbox:
    return Toolbox(FakeTelemetry(), FakeChangeHistory(), FakeClock(T0), timedelta(minutes=60))


async def test_default_consult_charts_the_error_rate_of_the_cited_service() -> None:
    model = ScriptedLanguageModel(console_calls=CALLS)

    draft = await model.consult("contexto", "pergunta", _toolbox(), TokenBudget(10_000))

    assert draft.charts == (ChartRequest(SERVICE, "taxa_de_erro", 60),)
    assert SERVICE in draft.text
    assert model.consultations == [("contexto", "pergunta")]


async def test_default_consult_without_a_cited_service_has_no_chart() -> None:
    draft = await ScriptedLanguageModel().consult("c", "p", _toolbox(), TokenBudget(10_000))

    assert draft.text.strip()
    assert draft.charts == ()


async def test_scripted_consult_returns_the_scripted_draft() -> None:
    scripted = ConsoleDraft("Sim.", ())

    model = ScriptedLanguageModel(console=scripted, console_calls=CALLS)

    assert await model.consult("c", "p", _toolbox(), TokenBudget(10_000)) == scripted

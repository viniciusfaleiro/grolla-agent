from datetime import UTC, datetime, timedelta
from uuid import uuid4

from grolla.domain.chat import ReplyStatus
from grolla.domain.console import ChartRequest, ConsoleMessage
from grolla.domain.incidents import EventType, IncidentEvent
from grolla_api.console_pages import (
    CHART_EMPTY,
    CHART_UNAVAILABLE,
    chart_title,
    chart_view,
    console_version,
    format_value,
)
from grolla_api.war_room import event_label

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
SESSION = uuid4()
LATENCY = ChartRequest("pedidos-api", "latencia_p99", 60)


def points(*values: float) -> list[tuple[datetime, float]]:
    start = NOW - timedelta(minutes=len(values) - 1)
    return [(start + timedelta(minutes=i), v) for i, v in enumerate(values)]


def test_chart_title_names_signal_service_and_window() -> None:
    assert chart_title(LATENCY) == "Latência p99 do pedidos-api, últimos 60 min"
    assert chart_title(ChartRequest("autorizador", "taxa_de_erro", 30)) == (
        "Taxa de erro do autorizador, últimos 30 min"
    )
    assert chart_title(ChartRequest("autorizador", "vazao", 5)).startswith("Vazão do autorizador")


def test_values_carry_the_unit() -> None:
    assert format_value("taxa_de_erro", 12.5) == "12,5%"
    assert format_value("taxa_de_erro", 3.0) == "3%"
    assert format_value("latencia_p99", 1240.4) == "1.240 ms"
    assert format_value("vazao", 12.34) == "12,3 req/s"


def test_chart_is_a_line_with_time_axis_and_values() -> None:
    view = chart_view(LATENCY, points(100, 340, 220))
    assert view.svg is not None
    svg = str(view.svg)
    assert svg.startswith("<svg")
    assert "<polyline" in svg
    assert "Latência p99 do pedidos-api" in svg  # título acessível
    assert "11:58" in svg
    assert "11:59" in svg
    assert "12:00" in svg
    assert "ms</text>" in svg  # valor com a unidade no eixo
    assert view.legend == "Latência p99 (ms) · agora 220 ms · máximo 340 ms"


def test_one_point_and_zeros_still_draw() -> None:
    view = chart_view(ChartRequest("a", "taxa_de_erro", 5), points(0))
    assert view.svg is not None
    assert "12:00" in str(view.svg)


def test_failure_and_empty_window_have_a_note() -> None:
    assert chart_view(LATENCY, None).note == CHART_UNAVAILABLE == "gráfico indisponível"
    empty = chart_view(LATENCY, [])
    assert empty.svg is None
    assert empty.note == CHART_EMPTY


def test_service_name_is_escaped_in_the_svg() -> None:
    view = chart_view(ChartRequest("<b>x</b>", "vazao", 5), points(1, 2))
    assert "<b>" not in str(view.svg)


def test_version_changes_when_a_reply_arrives() -> None:
    question = ConsoleMessage(1, SESSION, "Ana", "oi", NOW, reply_status=ReplyStatus.PENDING)
    answered = ConsoleMessage(1, SESSION, "Ana", "oi", NOW, reply_status=ReplyStatus.ANSWERED)
    reply = ConsoleMessage(2, SESSION, "agente", "ok", NOW, reply_to=1)
    assert console_version([question]) != console_version([answered, reply])


def test_timeline_says_opened_from_console() -> None:
    event = IncidentEvent(
        SESSION, NOW, EventType.INCIDENT_OPENED_MANUALLY, {"origin": "console"}, "Ana"
    )
    assert event_label(event, {}) == "Aberto pelo console"

"""Regras do console: título da sessão, gráficos válidos e o incidente aberto por uma pessoa."""

from uuid import UUID

import pytest
from grolla.domain.console import (
    MAX_CHART_MINUTES,
    MIN_CHART_MINUTES,
    TITLE_LIMIT,
    ChartRequest,
    session_title,
    valid_charts,
)
from grolla.domain.incidents import (
    Decision,
    EventType,
    IncidentState,
    NotificationKind,
    Severity,
    decide_manual,
)
from grolla.domain.memory import (
    Confidence,
    Memory,
    MemoryContext,
    MemoryKind,
    Source,
    SourceKind,
    SubjectKind,
    memory_text,
)
from grolla.testing.builders import T0

ID = UUID("00000000-0000-0000-0000-0000000000a1")


def test_title_is_the_question_in_one_line() -> None:
    assert session_title("  o autorizador\n  está saudável?  ") == "o autorizador está saudável?"


def test_long_title_is_cut_at_the_limit() -> None:
    title = session_title("palavra " * 30)

    assert len(title) == TITLE_LIMIT
    assert title.endswith("…")


def test_valid_charts_drop_unknown_signals_and_empty_services() -> None:
    charts = [
        ChartRequest("pedidos-api", "cpu", 60),
        ChartRequest("  ", "vazao", 60),
        ChartRequest(" pedidos-api ", "latencia_p99", 60),
    ]

    assert valid_charts(charts) == (ChartRequest("pedidos-api", "latencia_p99", 60),)


def test_valid_charts_clamp_the_window_and_keep_at_most_two() -> None:
    charts = [
        ChartRequest("a", "taxa_de_erro", 1),
        ChartRequest("b", "vazao", 10_000),
        ChartRequest("c", "vazao", 30),
    ]

    assert valid_charts(charts) == (
        ChartRequest("a", "taxa_de_erro", MIN_CHART_MINUTES),
        ChartRequest("b", "vazao", MAX_CHART_MINUTES),
    )


def _manual(severity: Severity = Severity.P2, fields: dict[str, str] | None = None) -> Decision:
    values = {"title": "pedidos-api lento", "service": "pedidos-api", "author": "Ana"}
    values.update(fields or {})
    return decide_manual(
        title=values["title"],
        service=values["service"],
        author=values["author"],
        severity=severity,
        origin="console",
        now=T0,
        page_url=lambda incident_id: f"https://grolla.interno/incidents/{incident_id}",
        new_id=lambda: ID,
    )


def test_manual_incident_is_informed_for_p2_with_the_author_and_the_analysis() -> None:
    decision = _manual()

    incident = decision.incident
    assert incident is not None
    assert (incident.id, incident.title, incident.services) == (
        ID,
        "pedidos-api lento",
        ("pedidos-api",),
    )
    assert incident.state is IncidentState.INFORMED
    assert (incident.detected_at, incident.firing) == (T0, frozenset())
    assert decision.request_analysis
    opened, *others = decision.events
    assert opened.type is EventType.INCIDENT_OPENED_MANUALLY
    assert opened.author == "Ana"
    assert opened.data == {"origin": "console", "author": "Ana", "severity": "P2"}
    assert [e.type for e in others] == [
        EventType.STATE_CHANGED,
        EventType.ANALYSIS_REQUESTED,
        EventType.NOTIFICATION_QUEUED,
    ]
    (card,) = decision.notifications
    assert card.kind is NotificationKind.INCIDENT_OPENED
    assert card.page_url == f"https://grolla.interno/incidents/{ID}"
    assert "Ana" in card.summary


def test_manual_p1_waits_detected_like_an_alert() -> None:
    decision = _manual(Severity.P1)

    assert decision.incident is not None
    assert decision.incident.state is IncidentState.DETECTED
    assert EventType.STATE_CHANGED not in [e.type for e in decision.events]


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("title", "informe o título"),
        ("service", "informe o serviço"),
        ("author", "informe o seu nome"),
    ],
)
def test_manual_incident_needs_title_service_and_author(field: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _manual(fields={field: "  "})


def test_note_taught_in_the_console_cites_the_console() -> None:
    record = Memory(
        id=ID,
        kind=MemoryKind.NOTE,
        subject="pedidos-api",
        subject_kind=SubjectKind.SERVICE,
        title="o batch sobe o p99",
        body="o batch sobe o p99",
        confidence=Confidence.CONFIRMED,
        source=Source(SourceKind.CHAT, "Ana", None),
        created_at=T0,
        updated_at=T0,
    )

    assert "console, por Ana" in memory_text(MemoryContext(notes=(record,)))

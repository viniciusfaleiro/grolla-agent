from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from grolla.domain.analysis import (
    Action,
    Analysis,
    AnalysisStatus,
    Evidence,
    Hypothesis,
    HypothesisVerdict,
    Verdict,
    Verification,
)
from grolla.domain.incidents import EventType, IncidentEvent, IncidentState
from grolla.testing.builders import T0, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla_api.app import Services, create_app
from grolla_api.war_room import (
    AUTHOR_COOKIE,
    StepCommand,
    event_kind,
    hypothesis_rows,
    situation_of,
)

NOW = T0 + timedelta(minutes=20)
SCALE = "kubectl scale deploy/antifraude-api --replicas=2 -n loja"


def analysis(status: AnalysisStatus = AnalysisStatus.COMPLETED, **changes: object) -> Analysis:
    base = Analysis(
        incident_id=uuid4(),
        status=status,
        requested_at=T0,
        started_at=T0,
        finished_at=T0 + timedelta(seconds=30),
        summary="O antifraude-api está com 0 réplicas.",
        evaluations=(),
        hypotheses=(
            Hypothesis("antifraude-api sem réplicas", 0.75, (Evidence("0 de 0 réplicas", None),)),
            Hypothesis("Bloqueio de rede", 0.15, ()),
        ),
        plan=("Conferir `kubectl get deploy -n loja`", "Ver os logs do antifraude"),
        partial_reason=None,
    )
    return replace(base, **changes)


def verification(**changes: object) -> Verification:
    base = Verification(
        incident_id=uuid4(),
        status=AnalysisStatus.COMPLETED,
        requested_at=T0,
        started_at=T0,
        finished_at=T0 + timedelta(minutes=1),
        verdicts=(
            HypothesisVerdict(
                "Antifraude-api  sem réplicas",
                Verdict.CONFIRMED,
                "0 de 0 réplicas e ConnectError nos logs.",
                (Evidence("97 logs de ConnectError", "https://logs/1"),),
            ),
            HypothesisVerdict("Bloqueio de rede", Verdict.REJECTED, "Nenhuma política mudou.", ()),
            HypothesisVerdict("Rollout restart às 15:02", Verdict.REJECTED, "Antes do erro.", ()),
        ),
        root_cause="Deployment antifraude-api com 0 réplicas desde 15:16.",
        actions=(
            Action(
                "Restaurar as réplicas: `kubectl scale deploy/antifraude-api --replicas=2 -n loja`",
                "Deployment antifraude-api",
                "O ConnectError some",
                None,
            ),
            Action("Levar as réplicas para o pipeline", "manifesto", "Não volta", None),
        ),
        missing="",
        evaluations=(),
        partial_reason=None,
    )
    return replace(base, **changes)


def test_situation_prefers_the_confirmed_root_cause_and_its_plan() -> None:
    s = situation_of(analysis(), verification())

    assert s.kind == "causa"
    assert s.title == "Antifraude-api  sem réplicas"
    assert s.text == "Deployment antifraude-api com 0 réplicas desde 15:16."
    assert not s.steps_from_analysis
    assert s.steps[0].commands == (StepCommand(SCALE, None, False),)
    assert s.steps[1].commands == ()


@pytest.mark.parametrize(
    ("ver", "kind"),
    [
        (None, "hipotese"),
        ("rodando", "validando"),
        ("sem_causa", "sem_causa"),
    ],
)
def test_situation_without_a_cause_shows_the_best_hypothesis(ver: str | None, kind: str) -> None:
    v = None
    if ver == "rodando":
        v = verification(status=AnalysisStatus.RUNNING, finished_at=None, root_cause=None)
    elif ver == "sem_causa":
        v = verification(root_cause=None, actions=(), missing="faltou o log do cluster")

    s = situation_of(analysis(), v)

    assert s.kind == kind
    assert s.title == "antifraude-api sem réplicas"
    assert s.text == "O antifraude-api está com 0 réplicas."
    assert s.confidence == 75
    assert s.steps_from_analysis
    assert s.steps[0].commands[0].command == "kubectl get deploy -n loja"
    if kind == "sem_causa":
        assert s.missing == "faltou o log do cluster"


def test_situation_while_investigating_and_without_analysis() -> None:
    assert situation_of(analysis(AnalysisStatus.RUNNING), None).kind == "investigando"
    assert situation_of(None, None).kind == "sem_analise"


def test_hypothesis_rows_join_each_hypothesis_with_its_verdict_once() -> None:
    rows = hypothesis_rows(analysis(), verification())

    assert [r.title for r in rows] == [
        "antifraude-api sem réplicas",
        "Bloqueio de rede",
        "Rollout restart às 15:02",  # veredito sem hipótese vai no fim
    ]
    assert rows[0].verdict == "confirmada"
    assert rows[0].confidence == 75
    assert rows[0].reason == "0 de 0 réplicas e ConnectError nos logs."
    assert len(rows[0].evidence) == 2  # da hipótese e do veredito
    assert rows[2].confidence is None


def test_hypothesis_rows_without_verification_use_the_first_evidence() -> None:
    rows = hypothesis_rows(analysis(), None)

    assert rows[0].verdict is None
    assert rows[0].reason == "0 de 0 réplicas"


@pytest.mark.parametrize(
    ("type_", "author", "kind"),
    [
        (EventType.ALERT_RECEIVED, "sistema", "alerta"),
        (EventType.INCIDENT_NORMALIZED, "sistema", "alerta"),
        (EventType.NOTIFICATION_DELIVERED, "sistema", "sistema"),
        (EventType.ANALYSIS_REQUESTED, "sistema", "sistema"),
        (EventType.ANALYSIS_COMPLETED, "agente", "grolla"),
        (EventType.ACTION_RECORDED, "Ana", "pessoa"),
    ],
)
def test_event_kind(type_: EventType, author: str, kind: str) -> None:
    assert event_kind(IncidentEvent(uuid4(), T0, type_, {}, author=author)) == kind


@pytest.fixture
def repository() -> InMemoryIncidentRepository:
    return InMemoryIncidentRepository()


@pytest.fixture
def ana(services: Services, repository: InMemoryIncidentRepository) -> TestClient:
    app = create_app(replace(services, repository=repository, clock=FakeClock(NOW)))
    client = TestClient(app, follow_redirects=False)
    client.cookies.set(AUTHOR_COOKIE, "Ana")
    return client


def test_war_room_reads_now_then_how_then_the_timeline(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = make_incident(state=IncidentState.HANDLING)
    repository.incidents = {incident.id: incident}
    repository.analyses = {incident.id: replace(analysis(), incident_id=incident.id)}
    repository.verifications = {incident.id: replace(verification(), incident_id=incident.id)}

    page = ana.get(f"/incidents/{incident.id}").text

    order = ['id="incident-status"', 'id="agora"', 'id="investigacao"', 'id="linha-do-tempo"']
    positions = [page.index(marker) for marker in order]
    assert positions == sorted(positions)
    assert 'data-copy="kubectl scale deploy/antifraude-api --replicas=2 -n loja"' in page
    assert "Depois: mais 1 passo" in page
    assert "O Grolla não executa nada" in page
    assert page.count("Deployment antifraude-api com 0 réplicas desde 15:16.") == 1


def test_bar_has_one_next_step_and_the_rare_actions_in_the_menu(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = make_incident(state=IncidentState.HANDLING)
    repository.incidents = {incident.id: incident}

    page = ana.get(f"/incidents/{incident.id}").text
    bar = page.split('id="next-step-main"')[1].split('id="mais-acoes"')[0]
    menu = page.split('id="mais-acoes"')[1].split("</div>")[0]

    assert bar.count('class="button-primary button-big"') == 1
    assert "Marcar mitigado" in bar
    assert 'aria-label="Mais ações"' in bar
    assert "Assumir como IC" in menu
    assert 'name="severidade"' in menu
    assert "Enviar transcrição" in menu
    assert "Descartar como falso positivo" in menu
    assert "Juntar com…" not in menu  # sem outro incidente aberto


def test_timeline_marks_each_event_kind_and_hides_system_ones_behind_a_toggle(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = make_incident()
    repository.incidents = {incident.id: incident}
    repository.events = [
        IncidentEvent(incident.id, T0, EventType.ALERT_RECEIVED, {}, author="sistema"),
        IncidentEvent(incident.id, T0, EventType.ANALYSIS_REQUESTED, {}, author="sistema"),
        IncidentEvent(incident.id, T0, EventType.ACTION_RECORDED, {"text": "x"}, author="Ana"),
    ]

    page = ana.get(f"/incidents/{incident.id}").text

    assert 'data-kind="alerta"' in page
    assert 'data-kind="sistema"' in page
    assert 'data-kind="pessoa"' in page
    assert "Mostrar 1 evento do sistema" in page
    assert 'data-filtro="pessoa"' in page
    assert 'class="record"' in page
    assert "data-reset" in page


def test_situation_without_a_confirmed_verdict_titles_with_the_first_sentence() -> None:
    v = verification(
        verdicts=(),
        root_cause="Limite de CPU em 25m. A revisão nova subiu às 20:40 e o p99 foi a 2 s.",
    )

    s = situation_of(analysis(), v)

    assert s.title == "Limite de CPU em 25m."
    assert s.text.startswith("Limite de CPU em 25m. A revisão nova")


def test_situation_does_not_repeat_the_title_below_it() -> None:
    s = situation_of(analysis(summary="antifraude-api sem réplicas"), None)

    assert s.title == "antifraude-api sem réplicas"
    assert s.text == ""


def test_situation_title_stops_at_the_first_comma() -> None:
    v = verification(
        verdicts=(
            HypothesisVerdict(
                "Limite de CPU muito baixo (25m contra 500m esperado) causando throttling, "
                "provavelmente por mudança manual fora do pipeline",
                Verdict.CONFIRMED,
                "",
                (),
            ),
        )
    )

    s = situation_of(analysis(), v)

    assert s.title == "Limite de CPU muito baixo (25m contra 500m esperado) causando throttling"

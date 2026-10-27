from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import grolla_api.app
import pytest
from fastapi.testclient import TestClient
from grolla.application import post_mortem
from grolla.domain.analysis import Analysis, AnalysisStatus, SeenBefore
from grolla.domain.incidents import EventType, Incident, IncidentEvent, IncidentState
from grolla.domain.metrics import Granularity, Period, PeriodMetrics
from grolla.domain.post_mortem import AgentEvaluation, PostMortem, PostMortemStatus
from grolla.testing.builders import make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla.testing.metrics import InMemoryMetricsReader
from grolla.testing.post_mortem import InMemoryPostMortemStore, make_action, make_post_mortem
from grolla_api.app import Services, create_app
from grolla_api.metrics_pages import evaluation_from, granularity_of, hit_rate, median_minutes
from grolla_api.war_room import AUTHOR_COOKIE

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
HTMX = {"HX-Request": "true"}
PAST = uuid4()


@pytest.fixture
def repository() -> InMemoryIncidentRepository:
    return InMemoryIncidentRepository()


@pytest.fixture
def store(repository: InMemoryIncidentRepository) -> InMemoryPostMortemStore:
    return InMemoryPostMortemStore(repository)


@pytest.fixture
def client(
    services: Services, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> TestClient:
    app = create_app(
        replace(
            services,
            repository=repository,
            post_mortems=store,
            metrics=InMemoryMetricsReader(repository, store),
            clock=FakeClock(NOW),
        )
    )
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def ana(client: TestClient) -> TestClient:
    client.cookies.set(AUTHOR_COOKIE, "Ana")
    return client


def with_seen_before(repository: InMemoryIncidentRepository) -> Incident:
    incident = make_incident()
    repository.incidents[incident.id] = incident
    repository.analyses[incident.id] = Analysis(
        incident_id=incident.id,
        status=AnalysisStatus.COMPLETED,
        requested_at=NOW,
        started_at=NOW,
        finished_at=NOW,
        summary="Pool esgotado.",
        evaluations=(),
        hypotheses=(),
        plan=(),
        partial_reason=None,
        seen_before=SeenBefore(PAST, "pedidos-api com 5xx", NOW, "mesmo erro de pool"),
    )
    return incident


def answer(incident_id: UUID, same: bool, author: str = "Ana", past: UUID = PAST) -> IncidentEvent:
    return IncidentEvent(
        incident_id=incident_id,
        at=NOW,
        type=EventType.SEEN_BEFORE_ANSWERED,
        data={"past": str(past), "same": same},
        author=author,
    )


# ---- "já vi isso": era o mesmo problema?


def test_seen_before_offers_the_two_answers_to_who_is_identified(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = with_seen_before(repository)

    page = ana.get(f"/incidents/{incident.id}").text

    assert f'hx-post="/incidents/{incident.id}/ja-vi-isso"' in page
    assert '<button name="mesmo" value="sim">Era o mesmo problema</button>' in page
    assert 'value="nao" class="button-quiet">Não era</button>' in page


def test_seen_before_hides_the_answers_without_a_name(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = with_seen_before(repository)

    page = client.get(f"/incidents/{incident.id}").text

    assert "Já vi isso:" in page
    assert "ja-vi-isso" not in page.replace('id="erro-ja-vi-isso"', "")


@pytest.mark.parametrize(
    ("same", "shown"),
    [
        (True, "Ana confirmou: era o mesmo problema"),
        (False, "Ana respondeu: não era o mesmo problema"),
    ],
)
def test_seen_before_shows_the_last_answer_and_the_timeline_label(
    client: TestClient, repository: InMemoryIncidentRepository, same: bool, shown: str
) -> None:
    incident = with_seen_before(repository)
    repository.events += [
        answer(incident.id, not same, author="Bia"),
        answer(incident.id, same),
        answer(incident.id, True, author="Caio", past=uuid4()),  # outro caso: não conta
    ]

    page = client.get(f"/incidents/{incident.id}").text

    assert f'<p class="seen-before-answer">{shown}</p>' in page
    label = "era o mesmo problema" if same else "não era o mesmo problema"
    assert f'<span class="event-label">Já vi isso: {label}</span>' in page


def test_answering_records_the_event_and_reloads_the_page(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = with_seen_before(repository)

    response = ana.post(f"/incidents/{incident.id}/ja-vi-isso", data={"mesmo": "sim"})

    assert response.status_code == 303
    assert response.headers["location"] == f"/incidents/{incident.id}"
    [event] = [e for e in repository.events if e.type is EventType.SEEN_BEFORE_ANSWERED]
    assert event.author == "Ana"
    assert event.data["same"] is True


def test_answering_with_htmx_navigates_by_hx_redirect(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = with_seen_before(repository)

    response = ana.post(f"/incidents/{incident.id}/ja-vi-isso", data={"mesmo": "nao"}, headers=HTMX)

    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == f"/incidents/{incident.id}"


def test_answering_needs_a_name(client: TestClient, repository: InMemoryIncidentRepository) -> None:
    incident = with_seen_before(repository)

    response = client.post(f"/incidents/{incident.id}/ja-vi-isso", data={"mesmo": "sim"})

    assert response.status_code == 401
    assert response.headers["HX-Retarget"] == "#erro-ja-vi-isso"


@pytest.mark.parametrize("value", ["", "talvez"])
def test_answering_needs_yes_or_no(
    ana: TestClient, repository: InMemoryIncidentRepository, value: str
) -> None:
    incident = with_seen_before(repository)

    response = ana.post(f"/incidents/{incident.id}/ja-vi-isso", data={"mesmo": value})

    assert response.status_code == 422
    assert "responda se era o mesmo problema" in response.text


def test_answering_without_seen_before_is_refused(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = make_incident()
    repository.incidents[incident.id] = incident

    response = ana.post(f"/incidents/{incident.id}/ja-vi-isso", data={"mesmo": "sim"})

    assert response.status_code == 422
    assert response.headers["HX-Retarget"] == "#erro-ja-vi-isso"


@pytest.mark.parametrize("incident_id", ["nao-e-uuid", str(uuid4())])
def test_answering_an_unknown_incident_is_404(ana: TestClient, incident_id: str) -> None:
    response = ana.post(f"/incidents/{incident_id}/ja-vi-isso", data={"mesmo": "sim"})

    assert response.status_code == 404


# ---- avaliação do agente no post mortem


async def resolved_with(
    repository: InMemoryIncidentRepository,
    store: InMemoryPostMortemStore,
    status: PostMortemStatus = PostMortemStatus.REVIEW,
    evaluation: AgentEvaluation | None = None,
) -> PostMortem:
    incident = make_incident(state=IncidentState.RESOLVED)
    repository.incidents[incident.id] = incident
    pm = make_post_mortem(incident.id, status=status)
    if evaluation is not None:
        pm = replace(pm, evaluation=evaluation)
    await store.save(pm, [])
    return pm


async def test_post_mortem_shows_the_evaluation_form_with_the_current_values(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    evaluation = AgentEvaluation(True, 8, False, "Bia", NOW)
    pm = await resolved_with(repository, store, evaluation=evaluation)

    page = ana.get(f"/postmortems/{pm.incident_id}").text

    assert f'hx-post="/postmortems/{pm.incident_id}/avaliacao"' in page
    assert "<dt>Hipótese principal certa</dt><dd>sim</dd>" in page
    assert "<dt>Minutos até a hipótese certa</dt><dd>8</dd>" in page
    assert "<dt>Memória ajudou</dt><dd>não</dd>" in page
    assert "Avaliado por Bia em" in page
    assert 'name="hipotese_certa" value="sim" checked' in page
    assert 'name="memoria_ajudou" value="nao" checked' in page
    assert 'name="minutos" min="0" step="1" inputmode="numeric" value="8"' in page
    assert "Editar a avaliação" in page


async def test_post_mortem_without_evaluation_invites_the_team(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    pm = await resolved_with(repository, store)

    page = ana.get(f"/postmortems/{pm.incident_id}").text

    assert "O time ainda não avaliou o agente." in page
    assert "<dt>Hipótese principal certa</dt><dd>—</dd>" in page
    assert 'name="hipotese_certa" value="" checked' in page
    assert "Avaliar o agente" in page


async def test_closed_post_mortem_shows_the_evaluation_without_the_form(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    evaluation = AgentEvaluation(True, 8, True, "Bia", NOW)
    pm = await resolved_with(repository, store, PostMortemStatus.CLOSED, evaluation)

    page = ana.get(f"/postmortems/{pm.incident_id}").text

    assert "<dt>Memória ajudou</dt><dd>sim</dd>" in page
    assert f'hx-post="/postmortems/{pm.incident_id}/avaliacao"' not in page


async def test_evaluating_saves_the_fields_with_the_author(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    pm = await resolved_with(repository, store, PostMortemStatus.APPROVED)

    response = ana.post(
        f"/postmortems/{pm.incident_id}/avaliacao",
        data={"hipotese_certa": "sim", "minutos": "8", "memoria_ajudou": "sim"},
        headers=HTMX,
    )

    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == f"/postmortems/{pm.incident_id}"
    saved = await store.get(pm.incident_id)
    assert saved is not None
    assert saved.evaluation == AgentEvaluation(True, 8, True, "Ana", NOW)


@pytest.mark.parametrize(
    ("form", "message"),
    [
        ({"hipotese_certa": "talvez"}, "responda sim, não ou deixe sem resposta"),
        ({"minutos": "oito"}, "minutos inválidos: use um número inteiro"),
    ],
)
async def test_evaluating_with_an_invalid_field_is_422(
    ana: TestClient,
    repository: InMemoryIncidentRepository,
    store: InMemoryPostMortemStore,
    form: dict[str, str],
    message: str,
) -> None:
    pm = await resolved_with(repository, store)

    response = ana.post(f"/postmortems/{pm.incident_id}/avaliacao", data=form)

    assert response.status_code == 422
    assert response.headers["HX-Retarget"] == "#erro-avaliacao"
    assert message in response.text


async def test_evaluating_a_closed_post_mortem_is_refused_by_the_domain(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    pm = await resolved_with(repository, store, PostMortemStatus.CLOSED)

    response = ana.post(f"/postmortems/{pm.incident_id}/avaliacao", data={"minutos": "8"})

    assert response.status_code == 422


async def test_evaluating_needs_a_name(
    client: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    pm = await resolved_with(repository, store)

    response = client.post(f"/postmortems/{pm.incident_id}/avaliacao", data={"minutos": "8"})

    assert response.status_code == 401


def test_evaluation_form_leaves_blank_as_no_answer() -> None:
    assert evaluation_from({"hipotese_certa": "nao", "minutos": "", "memoria_ajudou": ""}) == (
        AgentEvaluation(main_hypothesis_correct=False)
    )


async def test_approving_passes_the_repository_to_learn_from_the_post_mortem(
    ana: TestClient,
    repository: InMemoryIncidentRepository,
    store: InMemoryPostMortemStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def spy(**kwargs: Any) -> PostMortem:
        calls.append(kwargs)
        return await post_mortem.move_status(**kwargs)

    monkeypatch.setattr(grolla_api.app, "move_status", spy)
    incident = make_incident(state=IncidentState.RESOLVED)
    repository.incidents[incident.id] = incident
    action = replace(make_action(), owner="Ana", due=NOW.date())
    await store.save(
        make_post_mortem(incident.id, status=PostMortemStatus.REVIEW, actions=(action,)), []
    )

    response = ana.post(f"/postmortems/{incident.id}/status", data={"para": "aprovado"})

    assert response.status_code == 303
    assert calls[0]["repository"] is repository


# ---- /metricas


def period_row(**changes: Any) -> PeriodMetrics:
    fields: dict[str, Any] = {
        "period": Period(NOW.date(), NOW.date(), "out/2026"),
        "resolved": 0,
        "approved": 0,
        "evaluated": 0,
        "correct": 0,
        "median_minutes": None,
        "seen_before_confirmed": 0,
        "seen_before_rejected": 0,
        "memory_helped": 0,
    } | changes
    return PeriodMetrics(**fields)


async def test_metrics_page_shows_the_month_with_the_team_answers(
    client: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    for correct, minutes in ((True, 6), (True, 8), (True, 10), (False, None)):
        incident = replace(
            make_incident(state=IncidentState.RESOLVED), resolved_at=NOW - timedelta(days=2)
        )
        repository.incidents[incident.id] = incident
        pm = make_post_mortem(incident.id, status=PostMortemStatus.APPROVED, at=NOW)
        evaluation = AgentEvaluation(correct, minutes, correct, "Ana", NOW)
        await store.save(replace(pm, evaluation=evaluation), [])
        repository.events.append(answer(incident.id, correct))

    page = client.get("/metricas").text

    assert 'href="/metricas?por=mes" aria-current="page">Por mês</a>' in page
    assert '<th scope="row">out/2026</th>' in page
    assert "<td>75% (3 de 4)</td>" in page
    assert "<td>8 min</td>" in page
    assert '<a href="/metricas" aria-current="page">Métricas</a>' in page


def test_metrics_page_by_week_shows_six_periods_with_dashes(client: TestClient) -> None:
    page = client.get("/metricas?por=semana").text

    assert 'href="/metricas?por=semana" aria-current="page">Por semana</a>' in page
    assert page.count('<th scope="row">') == 6
    assert "<td>—</td>" in page


def test_metrics_formatting() -> None:
    assert hit_rate(period_row()) == "—"
    assert hit_rate(period_row(evaluated=3, correct=2)) == "66,7% (2 de 3)"
    assert median_minutes(period_row(median_minutes=7.5)) == "7,5 min"
    assert median_minutes(period_row()) == "—"
    assert granularity_of("semana") is Granularity.WEEK
    assert granularity_of("ano") is Granularity.MONTH

from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from grolla.domain.incidents import Incident, IncidentState
from grolla.domain.post_mortem import (
    AgentEvaluation,
    PostMortem,
    PostMortemStatus,
    SectionKey,
)
from grolla.testing.builders import T0, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla.testing.post_mortem import InMemoryPostMortemStore, make_action, make_post_mortem
from grolla_api.app import Services, create_app
from grolla_api.post_mortem_pages import first_sentence, outline_of, plan_status
from grolla_api.war_room import AUTHOR_COOKIE

NOW = T0 + timedelta(days=3)


@pytest.fixture
def repository() -> InMemoryIncidentRepository:
    return InMemoryIncidentRepository()


@pytest.fixture
def store(repository: InMemoryIncidentRepository) -> InMemoryPostMortemStore:
    return InMemoryPostMortemStore(repository)


@pytest.fixture
def ana(
    services: Services, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> TestClient:
    app = create_app(
        replace(services, repository=repository, post_mortems=store, clock=FakeClock(NOW))
    )
    client = TestClient(app, follow_redirects=False)
    client.cookies.set(AUTHOR_COOKIE, "Ana")
    return client


def resolved(repository: InMemoryIncidentRepository, title: str) -> Incident:
    incident = replace(make_incident(state=IncidentState.RESOLVED), title=title)
    repository.incidents[incident.id] = incident
    return incident


def with_summary(pm: PostMortem, body: str) -> PostMortem:
    sections = tuple(
        replace(s, body=body) if s.key is SectionKey.SUMMARY else s for s in pm.sections
    )
    return replace(pm, sections=sections)


def test_first_sentence_skips_bullets_and_stops_at_the_period() -> None:
    assert first_sentence("\n- O limite de CPU caiu para 50m. O p99 subiu.") == (
        "O limite de CPU caiu para 50m."
    )
    assert first_sentence("") == ""


def test_plan_status() -> None:
    assert plan_status(()) == "sem itens"
    ownerless = make_action(owner=None)
    assert plan_status((ownerless, ownerless)) == "2 itens sem dono ou prazo"
    done = make_action(owner="Ana", due=NOW.date())
    assert plan_status((done,)) == "0 de 1 concluídos"


def test_outline_says_what_is_missing() -> None:
    pm = make_post_mortem(make_incident().id, actions=(make_action(owner=None),))

    outline = {item.key: item for item in outline_of(pm, overdue=0)}

    assert outline[SectionKey.ACTIONS].missing == "1 sem dono"
    assert outline[SectionKey.AGENT].missing == "falta avaliar"
    assert outline[SectionKey.TIMELINE].who == "do log"


async def test_list_shows_totals_cause_plan_and_filters_the_unevaluated(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    pending = resolved(repository, "Latência alta na estoque-api")
    evaluated = resolved(repository, "Erros na pagamentos-api")
    await store.save(
        with_summary(
            make_post_mortem(pending.id, actions=(make_action(owner=None),)),
            "- O limite de CPU caiu para 50m. Depois o p99 subiu.",
        ),
        [],
    )
    signed = AgentEvaluation(main_hypothesis_correct=True, author="Bia", at=NOW)
    await store.save(
        replace(make_post_mortem(evaluated.id, status=PostMortemStatus.REVIEW), evaluation=signed),
        [],
    )

    page = ana.get("/postmortems").text
    only = ana.get("/postmortems?sem_avaliacao=1").text

    assert "Rascunhos para revisar" in page
    assert "O limite de CPU caiu para 50m." in page
    assert "Depois o p99 subiu" not in page
    assert "Plano: 1 item sem dono ou prazo" in page
    assert "agente sem avaliação" in page
    assert "Latência alta na estoque-api" in only
    assert "Erros na pagamentos-api" not in only
    assert "Limpar filtros" in only


async def test_document_has_the_next_step_numbers_and_the_outline(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository, "Latência alta na estoque-api")
    await store.save(make_post_mortem(incident.id, actions=(make_action(owner=None),)), [])

    page = ana.get(f"/postmortems/{incident.id}").text

    bar = page.split('class="pm-bar"')[1].split('class="pm-status-panel"')[0]
    assert 'class="button-primary button-big">Começar revisão' in bar
    assert "PDF depois de aprovar" in bar
    assert "TTA" in bar
    assert 'class="pm-outline"' in page
    assert "1 sem dono" in page
    assert "falta avaliar" in page
    assert '<span class="missing">sem dono</span>' in page

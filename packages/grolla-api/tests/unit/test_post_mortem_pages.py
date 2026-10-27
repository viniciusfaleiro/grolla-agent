from dataclasses import replace
from datetime import date, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from grolla.application.post_mortem import RenderError
from grolla.domain.incidents import Incident, IncidentState, Severity
from grolla.domain.post_mortem import (
    AGENT_AUTHOR,
    SECTION_TITLES,
    ActionItem,
    ActionKind,
    ActionStatus,
    PostMortem,
    PostMortemStatus,
    Section,
    SectionKey,
)
from grolla.testing.builders import T0, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla.testing.post_mortem import (
    FakeDocumentRenderer,
    InMemoryPostMortemStore,
    make_action,
    make_post_mortem,
)
from grolla_api.app import Services, create_app
from grolla_api.post_mortem_pages import changed_action, list_filters
from grolla_api.war_room import AUTHOR_COOKIE

NOW = T0 + timedelta(days=3)
TODAY = NOW.date()
HTMX = {"HX-Request": "true"}


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
        replace(services, repository=repository, post_mortems=store, clock=FakeClock(NOW))
    )
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def ana(client: TestClient) -> TestClient:
    client.cookies.set(AUTHOR_COOKIE, "Ana")
    return client


def resolved(
    repository: InMemoryIncidentRepository,
    *,
    severity: Severity = Severity.P2,
    services: tuple[str, ...] | None = None,
) -> Incident:
    incident = make_incident(state=IncidentState.RESOLVED, severity=severity, services=services)
    repository.incidents[incident.id] = incident
    return incident


def document(
    incident: Incident,
    status: PostMortemStatus = PostMortemStatus.DRAFT,
    actions: tuple[ActionItem, ...] = (),
) -> PostMortem:
    return make_post_mortem(incident.id, status=status, actions=actions)


async def saved(store: InMemoryPostMortemStore, pm: PostMortem) -> PostMortem:
    await store.save(pm, [])
    return pm


# Lista


async def test_list_shows_title_status_services_and_overdue_items(
    client: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository, severity=Severity.P1, services=("pedidos-api",))
    late = make_action(owner="Ana", due=TODAY - timedelta(days=1))
    await saved(store, document(incident, PostMortemStatus.REVIEW, (late,)))

    page = client.get("/postmortems").text

    assert f'href="/postmortems/{incident.id}"' in page
    assert incident.title in page
    assert "pedidos-api" in page
    assert "Em revisão" in page
    assert "1 item vencido" in page
    assert 'aria-current="page">Post mortems' in page


async def test_list_filters_by_service_and_status(
    client: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    wanted = resolved(repository, services=("pedidos-api",))
    other_service = resolved(repository, services=("captura",))
    other_status = resolved(repository, services=("pedidos-api",))
    await saved(store, document(wanted, PostMortemStatus.REVIEW))
    await saved(store, document(other_service, PostMortemStatus.REVIEW))
    await saved(store, document(other_status, PostMortemStatus.DRAFT))

    page = client.get("/postmortems?servico=pedidos-api&status=em_revisao").text

    assert f"/postmortems/{wanted.id}" in page
    assert f"/postmortems/{other_service.id}" not in page
    assert f"/postmortems/{other_status.id}" not in page
    assert 'value="pedidos-api"' in page
    assert "Limpar" in page


def test_empty_list_is_friendly(client: TestClient) -> None:
    page = client.get("/postmortems").text

    assert "Nenhum post mortem ainda" in page


def test_empty_filtered_list_offers_to_see_all(client: TestClient) -> None:
    page = client.get("/postmortems?severidade=P0").text

    assert "Nenhum post mortem com esses filtros" in page


def test_invalid_filters_are_ignored() -> None:
    filters = list_filters({"status": "x", "severidade": "P9", "de": "ontem", "ate": "2026-01-02"})

    assert filters.status is None
    assert filters.severity is None
    assert filters.since is None
    assert filters.until == date(2026, 1, 2)


# Página


async def test_page_shows_sections_in_template_order_with_rendered_text(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository)
    pm = document(incident)
    summary = Section(SectionKey.SUMMARY, "Pedidos **falharam** por 20 min", AGENT_AUTHOR, T0)
    pm = replace(pm, sections=(summary, *pm.sections[1:]))
    await saved(store, pm)

    page = ana.get(f"/postmortems/{incident.id}").text

    positions = [page.index(SECTION_TITLES[key]) for key in SectionKey]
    assert positions == sorted(positions)
    assert "<strong>falharam</strong>" in page
    assert "Começar revisão" in page
    assert "Baixar PDF" not in page
    assert f'hx-post="/postmortems/{incident.id}/secoes/resumo/regenerar"' in page
    assert "/secoes/metricas/regenerar" not in page
    assert "/secoes/timeline/regenerar" not in page


async def test_page_marks_overdue_items_and_offers_to_conclude(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository)
    late = make_action(owner="Ana", due=TODAY - timedelta(days=1))
    await saved(store, document(incident, PostMortemStatus.REVIEW, (late,)))

    page = ana.get(f"/postmortems/{incident.id}").text

    assert "1 item do plano vencido" in page
    assert "pm-item-overdue" in page
    assert "detectar antes" in page
    assert "Concluir" in page
    assert "Aprovar" in page


async def test_approved_page_offers_pdf_and_no_edit_or_regenerate(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository)
    await saved(store, document(incident, PostMortemStatus.APPROVED))

    page = ana.get(f"/postmortems/{incident.id}").text

    assert f'href="/postmortems/{incident.id}.pdf"' in page
    assert "Encerrar" in page
    assert "Voltar para revisão" in page
    assert "/regenerar" not in page
    assert "<summary>Editar</summary>" not in page


async def test_page_without_author_asks_the_name_and_hides_writes(
    client: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository)
    await saved(store, document(incident))

    page = client.get(f"/postmortems/{incident.id}").text

    assert "Como o time te chama?" in page
    assert "Começar revisão" not in page


def test_resolved_incident_without_post_mortem_says_the_draft_is_being_written(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = resolved(repository)

    page = client.get(f"/postmortems/{incident.id}").text

    assert "O rascunho está sendo escrito" in page


def test_open_incident_has_no_post_mortem_yet(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = make_incident(state=IncidentState.HANDLING)
    repository.incidents[incident.id] = incident

    page = client.get(f"/postmortems/{incident.id}").text

    assert "Ainda não há post mortem" in page


def test_unknown_incident_is_404(client: TestClient) -> None:
    assert client.get(f"/postmortems/{uuid4()}").status_code == 404
    assert client.get("/postmortems/nao-e-uuid").status_code == 404


def test_resolved_incident_page_links_the_post_mortem(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = resolved(repository)

    page = client.get(f"/incidents/{incident.id}").text

    assert f'href="/postmortems/{incident.id}">Post mortem</a>' in page


# Seções


async def test_editing_a_section_reloads_and_keeps_the_previous_version(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository)
    await saved(store, document(incident))
    url = f"/postmortems/{incident.id}/secoes/resumo"

    response = ana.post(url, data={"texto": "Resumo do time"}, headers=HTMX)

    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == f"/postmortems/{incident.id}"
    pm = await store.get(incident.id)
    assert pm is not None
    assert pm.status is PostMortemStatus.REVIEW
    versions = ana.get(f"{url}/versoes").text
    assert "Resumo executivo: texto do agente." in versions
    assert "</strong>, por Ana" in versions


async def test_plain_form_edit_redirects_with_303(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository)
    await saved(store, document(incident))

    response = ana.post(f"/postmortems/{incident.id}/secoes/impacto", data={"texto": "Novo"})

    assert response.status_code == 303
    assert response.headers["location"] == f"/postmortems/{incident.id}"


async def test_writes_without_author_are_401(
    client: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository)
    await saved(store, document(incident))

    response = client.post(f"/postmortems/{incident.id}/secoes/resumo", data={"texto": "x"})

    assert response.status_code == 401
    assert response.headers["HX-Retarget"] == "#erro-resumo"
    assert "informe seu nome" in response.text


async def test_editing_an_approved_post_mortem_is_refused(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository)
    await saved(store, document(incident, PostMortemStatus.APPROVED))

    response = ana.post(f"/postmortems/{incident.id}/secoes/resumo", data={"texto": "x"})

    assert response.status_code == 422
    assert response.headers["HX-Retarget"] == "#erro-resumo"


async def test_unknown_section_is_404(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository)
    await saved(store, document(incident))

    assert ana.post(f"/postmortems/{incident.id}/secoes/x", data={"texto": "y"}).status_code == 404
    assert ana.get(f"/postmortems/{incident.id}/secoes/x/versoes").status_code == 404


async def test_writes_on_a_missing_post_mortem_are_404(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = resolved(repository)

    response = ana.post(f"/postmortems/{incident.id}/status", data={"para": "em_revisao"})

    assert response.status_code == 404


async def test_versions_fragment_without_history(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository)
    await saved(store, document(incident))

    page = ana.get(f"/postmortems/{incident.id}/secoes/fatores/versoes").text

    assert "Sem versões anteriores." in page


async def test_regeneration_request_is_confirmed(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository)
    await saved(store, document(incident))
    url = f"/postmortems/{incident.id}/secoes/fatores/regenerar"

    response = ana.post(url, data={"instrucao": "incluir a falta de alarme no RDS"}, headers=HTMX)

    assert response.status_code == 202
    assert "o Grolla está regenerando esta seção" in response.text
    job = await store.claim(NOW)
    assert job is not None
    assert job.key is SectionKey.FACTORS
    assert job.instruction == "incluir a falta de alarme no RDS"
    assert job.author == "Ana"


async def test_regeneration_without_htmx_redirects(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository)
    await saved(store, document(incident))

    response = ana.post(f"/postmortems/{incident.id}/secoes/resumo/regenerar", data={})

    assert response.status_code == 303


async def test_regenerating_an_approved_post_mortem_is_refused(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository)
    await saved(store, document(incident, PostMortemStatus.APPROVED))

    response = ana.post(f"/postmortems/{incident.id}/secoes/resumo/regenerar", data={})

    assert response.status_code == 422


# Status


async def test_approving_without_owner_is_refused_with_a_message(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository)
    orphan = make_action("Alarme de conexões no RDS")
    await saved(store, document(incident, PostMortemStatus.REVIEW, (orphan,)))

    response = ana.post(f"/postmortems/{incident.id}/status", data={"para": "aprovado"})

    assert response.status_code == 422
    assert response.headers["HX-Retarget"] == "#erro-status"
    assert "Alarme de conexões no RDS" in response.text


async def test_approving_with_owner_and_due_moves_the_status(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository)
    ready = make_action(owner="Ana", due=TODAY + timedelta(days=7))
    await saved(store, document(incident, PostMortemStatus.REVIEW, (ready,)))

    response = ana.post(f"/postmortems/{incident.id}/status", data={"para": "aprovado"})

    assert response.status_code == 303
    pm = await store.get(incident.id)
    assert pm is not None
    assert pm.status is PostMortemStatus.APPROVED
    assert pm.status_by == "Ana"


async def test_invalid_target_status_is_422(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository)
    await saved(store, document(incident))

    response = ana.post(f"/postmortems/{incident.id}/status", data={"para": "publicado"})

    assert response.status_code == 422
    assert "status inválido" in response.text


# Plano de ação


async def test_adding_an_action_item(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository)
    await saved(store, document(incident))

    response = ana.post(
        f"/postmortems/{incident.id}/plano",
        data={"descricao": "Rollback automático", "tipo": "mitigar", "dono": "Bia", "prazo": ""},
    )

    assert response.status_code == 303
    pm = await store.get(incident.id)
    assert pm is not None
    (added,) = pm.actions
    assert added.description == "Rollback automático"
    assert added.kind is ActionKind.MITIGATE
    assert added.owner == "Bia"
    assert added.due is None


async def test_adding_an_item_with_invalid_kind_or_date_is_422(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository)
    await saved(store, document(incident))
    url = f"/postmortems/{incident.id}/plano"

    bad_kind = ana.post(url, data={"descricao": "x", "tipo": "rezar"})
    bad_date = ana.post(url, data={"descricao": "x", "tipo": "prevenir", "prazo": "amanhã"})

    assert bad_kind.status_code == 422
    assert "tipo inválido" in bad_kind.text
    assert bad_date.status_code == 422
    assert bad_date.headers["HX-Retarget"] == "#erro-plano"


async def test_concluding_an_item_keeps_the_other_fields(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository)
    open_item = make_action(owner="Ana", due=TODAY)
    await saved(store, document(incident, PostMortemStatus.REVIEW, (open_item,)))

    response = ana.post(
        f"/postmortems/{incident.id}/plano/{open_item.id}", data={"situacao": "concluido"}
    )

    assert response.status_code == 303
    pm = await store.get(incident.id)
    assert pm is not None
    assert pm.actions == (replace(open_item, status=ActionStatus.DONE),)


async def test_unknown_item_is_404(
    ana: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository)
    await saved(store, document(incident))

    response = ana.post(f"/postmortems/{incident.id}/plano/{uuid4()}", data={"situacao": "aceito"})

    assert response.status_code == 404


def test_changed_action_clears_owner_and_due_when_sent_empty() -> None:
    current = make_action(owner="Ana", due=TODAY)

    fields = changed_action(
        current, {"descricao": "Novo", "tipo": "prevenir", "dono": "", "prazo": ""}
    )

    assert fields.description == "Novo"
    assert fields.kind is ActionKind.PREVENT
    assert fields.owner is None
    assert fields.due is None
    assert fields.status is ActionStatus.OPEN


# PDF


async def test_pdf_of_an_approved_post_mortem(
    client: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository)
    await saved(store, document(incident, PostMortemStatus.APPROVED))

    response = client.get(f"/postmortems/{incident.id}.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="post-mortem-{incident.id}.pdf"'
    )
    assert response.content


async def test_draft_has_no_pdf(
    client: TestClient, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository)
    await saved(store, document(incident))

    assert client.get(f"/postmortems/{incident.id}.pdf").status_code == 409


async def test_pdf_render_failure_is_503(
    services: Services, repository: InMemoryIncidentRepository, store: InMemoryPostMortemStore
) -> None:
    incident = resolved(repository)
    await saved(store, document(incident, PostMortemStatus.CLOSED))
    app = create_app(
        replace(
            services,
            repository=repository,
            post_mortems=store,
            renderer=FakeDocumentRenderer(RenderError("sem fonte")),
        )
    )

    response = TestClient(app).get(f"/postmortems/{incident.id}.pdf")

    assert response.status_code == 503

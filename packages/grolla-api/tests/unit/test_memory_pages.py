from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from grolla.application.memory import NO_BUDGET, ServiceStudy, StudyMode, StudyStatus
from grolla.domain.analysis import Analysis, AnalysisStatus, SeenBefore
from grolla.domain.memory import (
    AGENT_AUTHOR,
    ENVIRONMENT,
    Confidence,
    Memory,
    MemoryKind,
    Source,
    SourceKind,
    SubjectKind,
)
from grolla.testing.builders import T0, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla.testing.memory import InMemoryLearningQueue, InMemoryMemoryStore, InMemoryStudyQueue
from grolla_api.app import Services, create_app
from grolla_api.memory_pages import source_of
from grolla_api.war_room import AUTHOR_COOKIE, rich_text

NOW = T0 + timedelta(days=2)
HTMX = {"HX-Request": "true"}


@pytest.fixture
def store() -> InMemoryMemoryStore:
    return InMemoryMemoryStore()


@pytest.fixture
def studies() -> InMemoryStudyQueue:
    return InMemoryStudyQueue()


@pytest.fixture
def learnings(repository: InMemoryIncidentRepository) -> InMemoryLearningQueue:
    return InMemoryLearningQueue(repository)


@pytest.fixture
def repository() -> InMemoryIncidentRepository:
    return InMemoryIncidentRepository()


@pytest.fixture
def services(
    services: Services,
    store: InMemoryMemoryStore,
    studies: InMemoryStudyQueue,
    learnings: InMemoryLearningQueue,
    repository: InMemoryIncidentRepository,
) -> Services:
    return replace(
        services,
        memory=store,
        studies=studies,
        learning_queue=learnings,
        repository=repository,
        clock=FakeClock(NOW),
    )


@pytest.fixture
def client(services: Services) -> TestClient:
    return TestClient(create_app(services), follow_redirects=False)


@pytest.fixture
def ana(client: TestClient) -> TestClient:
    client.cookies.set(AUTHOR_COOKIE, "Ana")
    return client


def make_memory(  # noqa: PLR0913 — cada campo do registro é ajustável no teste
    *,
    kind: MemoryKind = MemoryKind.DOSSIER,
    subject: str = "pedidos-api",
    subject_kind: SubjectKind = SubjectKind.SERVICE,
    body: str = "## O que faz\nRecebe os pedidos.",
    confidence: Confidence = Confidence.LEARNED,
    source: Source | None = None,
    **changes: object,
) -> Memory:
    memory = Memory(
        id=uuid4(),
        kind=kind,
        subject=subject,
        subject_kind=subject_kind,
        title=f"{kind} de {subject}",
        body=body,
        confidence=confidence,
        source=source or Source(SourceKind.STUDY, AGENT_AUTHOR, "https://repo.interno/pedidos"),
        created_at=T0,
        updated_at=T0 + timedelta(hours=1),
    )
    return replace(memory, **changes)


async def remember(store: InMemoryMemoryStore, *records: Memory) -> None:
    await store.save([(record, None) for record in records])


# ---- barra


def test_the_bar_links_to_the_memory(client: TestClient) -> None:
    page = client.get("/incidents").text

    assert '<a href="/memoria">Memória</a>' in page


# ---- /memoria


def test_empty_memory_says_the_environment_was_not_studied_yet(client: TestClient) -> None:
    page = client.get("/memoria").text

    assert '<a href="/memoria" aria-current="page">Memória</a>' in page
    assert "Ainda não estudei o ambiente" in page
    assert 'action="/author"' in page  # sem nome, pede o nome antes das ações
    assert 'hx-post="/memoria/estudar"' not in page


def test_with_a_name_the_page_offers_to_study_and_to_relearn(ana: TestClient) -> None:
    page = ana.get("/memoria").text

    assert 'hx-post="/memoria/estudar"' in page
    assert 'popovertarget="reaprender"' in page
    assert 'hx-post="/memoria/reaprender"' in page
    assert 'name="confirmar" value="sim"' in page
    assert "Fica</strong> o histórico dos incidentes" in page


def test_study_turned_off_hides_the_buttons_and_says_why(services: Services) -> None:
    client = TestClient(create_app(replace(services, study_enabled=False)))
    client.cookies.set(AUTHOR_COOKIE, "Ana")

    page = client.get("/memoria").text

    assert "memory.study" in page
    assert 'hx-post="/memoria/estudar"' not in page


async def test_home_shows_the_environment_dossier_and_subjects_by_kind(
    client: TestClient, store: InMemoryMemoryStore
) -> None:
    await remember(
        store,
        make_memory(
            subject=ENVIRONMENT, subject_kind=SubjectKind.ENVIRONMENT, body="## Mapa\n<b>2</b> APIs"
        ),
        make_memory(subject="pedidos-api"),
        make_memory(subject="grolla-lab-db", subject_kind=SubjectKind.INTEGRATION),
    )

    page = client.get("/memoria").text

    assert '<p class="rich-heading">Mapa</p>' in page
    assert "&lt;b&gt;2&lt;/b&gt; APIs" in page
    assert "Ainda não estudei" not in page
    assert page.index("Serviços") < page.index("Integrações")
    assert '<a href="/memoria/pedidos-api">pedidos-api</a>' in page
    assert '<a href="/memoria/grolla-lab-db">grolla-lab-db</a>' in page


async def test_forgotten_environment_dossier_points_to_the_history(
    client: TestClient, store: InMemoryMemoryStore
) -> None:
    await remember(
        store,
        make_memory(
            subject=ENVIRONMENT, subject_kind=SubjectKind.ENVIRONMENT, body="antigo", active=False
        ),
    )

    page = client.get("/memoria").text

    assert "foi esquecido" in page
    assert "antigo" not in page


async def test_home_shows_the_last_study_and_each_service(
    client: TestClient, studies: InMemoryStudyQueue
) -> None:
    await studies.request(StudyMode.FULL, "Ana", NOW)
    run = await studies.claim(NOW)
    assert run is not None
    for study in (
        ServiceStudy("pedidos-api", NOW, ok=True, reason=None),
        ServiceStudy("captura-api", None, ok=False, reason=NO_BUDGET),
        ServiceStudy("estorno-api", None, ok=False, reason="repositório não encontrado"),
    ):
        await studies.service_done(run, study, ())

    page = client.get("/memoria").text

    assert "Estudo completo" in page
    assert "estudando agora" in page
    assert "Ana" in page
    assert "Estudados" in page
    assert "pedidos-api" in page
    assert "Pendentes" in page
    assert "captura-api" in page
    assert "Falharam" in page
    assert "repositório não encontrado" in page


async def test_home_lists_failed_learnings_with_the_incident(
    client: TestClient,
    learnings: InMemoryLearningQueue,
    repository: InMemoryIncidentRepository,
) -> None:
    incident = make_incident()
    repository.incidents[incident.id] = incident
    repository.learning_requests[incident.id] = NOW
    for _ in range(2):
        job = await learnings.claim(NOW)
        assert job is not None
        await learnings.finish(job, ok=False, reason="o LLM falhou", usages=(), now=NOW)

    page = client.get("/memoria").text

    assert "Aprendizados que falharam" in page
    assert f'href="/incidents/{incident.id}"' in page
    assert "o LLM falhou" in page


def test_open_subject_form_goes_to_the_subject_page(client: TestClient) -> None:
    response = client.get("/memoria", params={"assunto": " captura api "})

    assert response.status_code == 303
    assert response.headers["location"] == "/memoria/captura%20api"


# ---- estudar e reaprender


async def test_study_again_asks_a_full_study_and_reloads(
    ana: TestClient, studies: InMemoryStudyQueue
) -> None:
    response = ana.post("/memoria/estudar", headers=HTMX)

    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == "/memoria"
    run = await studies.last()
    assert run is not None
    assert (run.mode, run.author, run.status) == (StudyMode.FULL, "Ana", StudyStatus.PENDING)


def test_without_htmx_writes_answer_with_a_redirect(ana: TestClient) -> None:
    response = ana.post("/memoria/estudar")

    assert response.status_code == 303
    assert response.headers["location"] == "/memoria"


def test_study_already_queued_is_said_in_the_page(ana: TestClient) -> None:
    ana.post("/memoria/estudar")

    response = ana.post("/memoria/estudar", headers=HTMX)

    assert response.status_code == 409
    assert "já tem um estudo na fila" in response.text
    assert response.headers["HX-Retarget"] == "#erro-estudo"


@pytest.mark.parametrize("path", ["/memoria/estudar", "/memoria/reaprender"])
async def test_study_without_a_name_is_refused(
    client: TestClient, studies: InMemoryStudyQueue, path: str
) -> None:
    response = client.post(path, data={"confirmar": "sim"})

    assert response.status_code == 401
    assert "informe seu nome" in response.text
    assert await studies.last() is None


async def test_relearn_without_confirmation_is_refused(
    ana: TestClient, studies: InMemoryStudyQueue
) -> None:
    response = ana.post("/memoria/reaprender")

    assert response.status_code == 422
    assert "confirme" in response.text
    assert response.headers["HX-Retarget"] == "#erro-reaprender"
    assert await studies.last() is None


async def test_relearn_forgets_dossiers_keeps_episodes_and_confirmed_notes(
    ana: TestClient, store: InMemoryMemoryStore, studies: InMemoryStudyQueue
) -> None:
    dossier = make_memory()
    episode = make_memory(kind=MemoryKind.EPISODE, incident_id=uuid4())
    note = make_memory(kind=MemoryKind.NOTE, confidence=Confidence.CONFIRMED)
    await remember(store, dossier, episode, note)

    response = ana.post("/memoria/reaprender", data={"confirmar": "sim"})

    assert response.status_code == 303
    run = await studies.last()
    assert run is not None
    assert (run.mode, run.author) == (StudyMode.RELEARN, "Ana")
    assert {m.id for m in await store.all_active()} == {episode.id, note.id}


# ---- /memoria/{assunto}


async def test_subject_page_shows_dossier_episodes_and_notes_with_source(
    client: TestClient, store: InMemoryMemoryStore
) -> None:
    incident_id = uuid4()
    await remember(
        store,
        make_memory(body="Recebe os pedidos do **checkout**."),
        make_memory(
            kind=MemoryKind.EPISODE,
            body="Pool esgotado depois do deploy.",
            noise=True,
            incident_id=incident_id,
            source=Source(SourceKind.INCIDENT, AGENT_AUTHOR, str(incident_id)),
        ),
        make_memory(
            kind=MemoryKind.NOTE,
            body="p99 alto às 3h é o batch",
            confidence=Confidence.CONFIRMED,
            source=Source(SourceKind.CHAT, "Bia", str(incident_id)),
        ),
    )

    page = client.get("/memoria/pedidos-api").text

    assert "<h1>pedidos-api</h1>" in page
    assert "Recebe os pedidos do <strong>checkout</strong>." in page
    assert '<span class="badge badge-aprendido">aprendido</span>' in page
    assert '<span class="badge badge-confirmado">confirmado</span>' in page
    assert "ruído" in page
    assert f'href="/incidents/{incident_id}"' in page
    assert "chat, por Bia" in page
    assert 'href="https://repo.interno/pedidos"' in page  # link consultado pelo estudo
    assert 'action="/author"' in page
    assert "/confirmar" not in page  # sem nome não há ações


async def test_with_a_name_each_record_can_be_edited_confirmed_and_forgotten(
    ana: TestClient, store: InMemoryMemoryStore
) -> None:
    dossier = make_memory()
    await remember(store, dossier)

    page = ana.get("/memoria/pedidos-api").text

    assert f'hx-post="/memoria/registros/{dossier.id}"' in page
    assert f'hx-post="/memoria/registros/{dossier.id}/confirmar"' in page
    assert f'hx-post="/memoria/registros/{dossier.id}/esquecer"' in page
    assert f'hx-get="/memoria/registros/{dossier.id}/versoes"' in page
    assert 'hx-post="/memoria/pedidos-api/notas"' in page


async def test_forgotten_record_is_faded_and_cannot_be_forgotten_again(
    ana: TestClient, store: InMemoryMemoryStore
) -> None:
    note = make_memory(kind=MemoryKind.NOTE, active=False)
    await remember(store, note)

    page = ana.get("/memoria/pedidos-api").text

    assert "record-forgotten" in page
    assert "esquecido" in page
    assert f"/memoria/registros/{note.id}/esquecer" not in page


def test_subject_without_records_accepts_a_note_and_asks_its_kind(ana: TestClient) -> None:
    page = ana.get("/memoria/captura-api").text

    assert "Ainda não há dossiê de captura-api" in page
    assert 'name="tipo"' in page
    assert 'hx-post="/memoria/captura-api/notas"' in page


async def test_note_added_on_the_page_is_confirmed_with_the_author(
    ana: TestClient, store: InMemoryMemoryStore
) -> None:
    response = ana.post(
        "/memoria/captura-api/notas",
        data={"texto": "depende do autorizador e do Redis de sessão", "tipo": "integracao"},
        headers=HTMX,
    )

    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == "/memoria/captura-api"
    [note] = await store.of_subject("captura-api")
    assert note.kind is MemoryKind.NOTE
    assert note.subject_kind is SubjectKind.INTEGRATION
    assert note.confidence is Confidence.CONFIRMED
    assert note.source.author == "Ana"
    assert "escrito por Ana" in ana.get("/memoria/captura-api").text


async def test_note_takes_the_kind_of_the_existing_subject(
    ana: TestClient, store: InMemoryMemoryStore
) -> None:
    await remember(store, make_memory(subject="pedidos-api"))

    ana.post("/memoria/pedidos-api/notas", data={"texto": "usa Postgres", "tipo": "integracao"})

    notes = [m for m in await store.of_subject("pedidos-api") if m.kind is MemoryKind.NOTE]
    assert [n.subject_kind for n in notes] == [SubjectKind.SERVICE]


async def test_empty_note_is_refused_with_a_message(
    ana: TestClient, store: InMemoryMemoryStore
) -> None:
    response = ana.post("/memoria/captura-api/notas", data={"texto": "  "}, headers=HTMX)

    assert response.status_code == 422
    assert response.headers["HX-Retarget"] == "#erro-nota"
    assert await store.of_subject("captura-api") == []


async def test_note_without_a_name_is_refused(
    client: TestClient, store: InMemoryMemoryStore
) -> None:
    response = client.post("/memoria/captura-api/notas", data={"texto": "oi"})

    assert response.status_code == 401
    assert await store.of_subject("captura-api") == []


def test_too_long_subject_is_not_found(client: TestClient) -> None:
    assert client.get("/memoria/" + "x" * 101).status_code == 404


# ---- editar, confirmar, esquecer, restaurar


async def test_edit_confirms_the_record_and_keeps_the_previous_version(
    ana: TestClient, store: InMemoryMemoryStore
) -> None:
    dossier = make_memory(body="antigo")
    await remember(store, dossier)

    response = ana.post(f"/memoria/registros/{dossier.id}", data={"texto": "novo"}, headers=HTMX)

    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == "/memoria/pedidos-api"
    edited = await store.get(dossier.id)
    assert edited is not None
    assert (edited.body, edited.confidence) == ("novo", Confidence.CONFIRMED)
    [version] = await store.versions(dossier.id)
    assert (version.body, version.author) == ("antigo", "Ana")


async def test_confirm_and_forget(ana: TestClient, store: InMemoryMemoryStore) -> None:
    note = make_memory(kind=MemoryKind.NOTE)
    await remember(store, note)

    ana.post(f"/memoria/registros/{note.id}/confirmar")
    confirmed = await store.get(note.id)
    ana.post(f"/memoria/registros/{note.id}/esquecer")
    forgotten = await store.get(note.id)

    assert confirmed is not None
    assert confirmed.confidence is Confidence.CONFIRMED
    assert forgotten is not None
    assert not forgotten.active


async def test_versions_fragment_lists_each_version_and_restores_one(
    ana: TestClient, store: InMemoryMemoryStore
) -> None:
    dossier = make_memory(body="primeiro")
    await remember(store, dossier)
    ana.post(f"/memoria/registros/{dossier.id}", data={"texto": "segundo"})
    [version] = await store.versions(dossier.id)

    fragment = ana.get(f"/memoria/registros/{dossier.id}/versoes").text

    assert "primeiro" in fragment
    assert "por Ana" in fragment
    assert f'value="{version.at.isoformat()}"' in fragment
    response = ana.post(
        f"/memoria/registros/{dossier.id}/restaurar", data={"versao": version.at.isoformat()}
    )
    assert response.status_code == 303
    restored = await store.get(dossier.id)
    assert restored is not None
    assert restored.body == "primeiro"


async def test_record_without_versions_says_so(
    client: TestClient, store: InMemoryMemoryStore
) -> None:
    dossier = make_memory()
    await remember(store, dossier)

    fragment = client.get(f"/memoria/registros/{dossier.id}/versoes").text

    assert "Sem versões anteriores" in fragment
    assert "Restaurar" not in fragment


@pytest.mark.parametrize("versao", ["ontem", (T0 - timedelta(days=9)).isoformat()])
async def test_restoring_a_version_that_does_not_exist_is_refused(
    ana: TestClient, store: InMemoryMemoryStore, versao: str
) -> None:
    dossier = make_memory()
    await remember(store, dossier)

    response = ana.post(
        f"/memoria/registros/{dossier.id}/restaurar", data={"versao": versao}, headers=HTMX
    )

    assert response.status_code == 422
    assert response.headers["HX-Retarget"] == f"#erro-{dossier.id}"


async def test_empty_edit_is_refused_with_a_message(
    ana: TestClient, store: InMemoryMemoryStore
) -> None:
    dossier = make_memory(body="antigo")
    await remember(store, dossier)

    response = ana.post(f"/memoria/registros/{dossier.id}", data={"texto": ""}, headers=HTMX)

    assert response.status_code == 422
    assert response.headers["HX-Retarget"] == f"#erro-{dossier.id}"
    unchanged = await store.get(dossier.id)
    assert unchanged is not None
    assert unchanged.body == "antigo"


@pytest.mark.parametrize("action", ["", "/confirmar", "/esquecer", "/restaurar"])
async def test_record_writes_without_a_name_are_refused(
    client: TestClient, store: InMemoryMemoryStore, action: str
) -> None:
    note = make_memory(kind=MemoryKind.NOTE)
    await remember(store, note)

    response = client.post(f"/memoria/registros/{note.id}{action}", data={"texto": "x"})

    assert response.status_code == 401
    assert await store.get(note.id) == note


@pytest.mark.parametrize("memory_id", [str(uuid4()), "nao-e-um-id"])
@pytest.mark.parametrize("path", ["", "/confirmar", "/versoes"])
def test_unknown_record_is_not_found(ana: TestClient, memory_id: str, path: str) -> None:
    url = f"/memoria/registros/{memory_id}{path}"
    response = ana.get(url) if path == "/versoes" else ana.post(url, data={"texto": "x"})

    assert response.status_code == 404


# ---- "já vi isso" na página do incidente


def test_incident_page_shows_seen_before_with_the_past_incident(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = make_incident()
    repository.incidents[incident.id] = incident
    past = uuid4()
    repository.analyses[incident.id] = Analysis(
        incident_id=incident.id,
        status=AnalysisStatus.COMPLETED,
        requested_at=T0,
        started_at=T0,
        finished_at=T0,
        summary="Pool esgotado.",
        evaluations=(),
        hypotheses=(),
        plan=(),
        partial_reason=None,
        seen_before=SeenBefore(
            incident_id=past,
            title="pedidos-api com 5xx",
            at=T0.replace(month=10, day=20),
            reason="mesmo erro de pool após deploy com migration",
        ),
    )

    page = client.get(f"/incidents/{incident.id}").text

    assert f'Já vi isso: <a href="/incidents/{past}">pedidos-api com 5xx</a>' in page
    assert "(20/10)" in page
    assert "mesmo erro de pool após deploy com migration" in page


# ---- apresentação


def test_rich_text_turns_markdown_headings_into_section_titles() -> None:
    html = rich_text("## Alertas\n- **5xx**: olhar o pool\n#sem-espaco")

    assert html == (
        '<p class="rich-heading">Alertas</p>'
        "<ul><li><strong>5xx</strong>: olhar o pool</li></ul><p>#sem-espaco</p>"
    )


def test_source_of_a_person_and_of_a_study_with_links() -> None:
    person = make_memory(source=Source(SourceKind.PERSON, "Ana"))
    study = make_memory(source=Source(SourceKind.STUDY, AGENT_AUTHOR, "https://a\n\nhttps://b"))

    assert source_of(person) == ("escrito por Ana", [])
    label, links = source_of(study)
    assert label == "estudo do ambiente"
    assert [link.url for link in links] == ["https://a", "https://b"]

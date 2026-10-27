from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import timedelta
from uuid import UUID

import grolla_api.app
import pytest
from fastapi.testclient import TestClient
from grolla.application import war_room
from grolla.application.clock import Clock
from grolla.application.incidents import IncidentRepository
from grolla.application.war_room import IncidentNotFoundError
from grolla.domain.analysis import Analysis, AnalysisStatus, Evaluation, Hypothesis
from grolla.domain.chat import AGENT_AUTHOR, ChatMessage, ReplyStatus
from grolla.domain.incidents import EventType, Incident, IncidentEvent, IncidentState, Severity
from grolla.domain.war_room import (
    AssumeCommander,
    ChangeSeverity,
    DiscardHypothesis,
    DiscardIncident,
    HumanAction,
    InvalidActionError,
    MarkMitigated,
    RecordAction,
    Reopen,
    Resolve,
)
from grolla.testing.builders import T0, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla_api.app import Services, create_app
from grolla_api.war_room import AUTHOR_COOKIE, ago, format_duration, initials, rich_text


@dataclass
class WarRoomStub:
    """Espião dos casos de uso reais `act` e `ask`: registra a chamada e delega."""

    repository: InMemoryIncidentRepository
    actions: list[tuple[HumanAction, str]] = field(default_factory=list[tuple[HumanAction, str]])
    questions: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])

    async def act(  # noqa: PLR0913 — mesma assinatura do caso de uso
        self,
        *,
        repository: IncidentRepository,
        incident_id: UUID,
        action: HumanAction,
        author: str,
        clock: Clock,
        page_url: Callable[[UUID], str],
    ) -> Incident:
        self.actions.append((action, author))
        return await war_room.act(
            repository=repository,
            incident_id=incident_id,
            action=action,
            author=author,
            clock=clock,
            page_url=page_url,
        )

    async def ask(
        self,
        repository: IncidentRepository,
        incident_id: UUID,
        author: str,
        text: str,
        clock: Clock,
    ) -> None:
        self.questions.append((author, text))
        await war_room.ask(repository, incident_id, author, text, clock)


NOW = T0 + timedelta(minutes=12)


@pytest.fixture
def repository() -> InMemoryIncidentRepository:
    return InMemoryIncidentRepository()


@pytest.fixture(autouse=True)
def stub(repository: InMemoryIncidentRepository, monkeypatch: pytest.MonkeyPatch) -> WarRoomStub:
    stub = WarRoomStub(repository)
    monkeypatch.setattr(grolla_api.app, "act", stub.act)
    monkeypatch.setattr(grolla_api.app, "ask", stub.ask)
    return stub


@pytest.fixture
def client(services: Services, repository: InMemoryIncidentRepository) -> TestClient:
    return TestClient(
        create_app(replace(services, repository=repository, clock=FakeClock(NOW))),
        follow_redirects=False,
    )


@pytest.fixture
def ana(client: TestClient) -> TestClient:
    client.cookies.set(AUTHOR_COOKIE, "Ana")
    return client


def add_incident(repository: InMemoryIncidentRepository, **changes: object) -> Incident:
    incident = replace(make_incident(severity=Severity.P1), **changes)
    repository.incidents[incident.id] = incident
    return incident


# ---- autor


def test_first_visit_asks_for_the_name_before_actions_and_chat(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository)

    page = client.get(f"/incidents/{incident.id}").text

    assert 'action="/author"' in page
    assert "Informe seu nome" in page
    assert "/actions/" not in page
    assert f'hx-post="/incidents/{incident.id}/chat"' not in page


def test_with_the_cookie_the_page_shows_who_you_are_and_the_actions(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository)

    page = ana.get(f"/incidents/{incident.id}").text

    assert "Você é <strong>Ana</strong>" in page
    assert 'popovertarget="trocar-nome">(trocar)</button>' in page
    assert f'hx-post="/incidents/{incident.id}/actions/assume"' in page
    assert f'hx-post="/incidents/{incident.id}/chat"' in page


def test_author_route_sets_the_cookie_and_redirects_back(client: TestClient) -> None:
    response = client.post("/author", data={"nome": " Ana Lúcia ", "voltar": "/incidents/x"})

    assert response.status_code == 303
    assert response.headers["location"] == "/incidents/x"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Max-Age=2592000" in cookie
    assert client.cookies[AUTHOR_COOKIE] == "Ana%20L%C3%BAcia"


@pytest.mark.parametrize("target", ["https://fora.example", "//fora.example", ""])
def test_author_route_only_redirects_inside_the_site(client: TestClient, target: str) -> None:
    response = client.post("/author", data={"nome": "Ana", "voltar": target})

    assert response.headers["location"] == "/incidents"


def test_author_route_refuses_an_empty_name(client: TestClient) -> None:
    response = client.post("/author", data={"nome": "  "})

    assert response.status_code == 422
    assert AUTHOR_COOKIE not in client.cookies


def test_encoded_name_from_the_cookie_is_the_author(
    client: TestClient, repository: InMemoryIncidentRepository, stub: WarRoomStub
) -> None:
    incident = add_incident(repository)
    client.post("/author", data={"nome": "Ana Lúcia"})

    client.post(f"/incidents/{incident.id}/actions/assume")

    assert stub.actions == [(AssumeCommander(), "Ana Lúcia")]


# ---- ações


@pytest.mark.parametrize("path", ["actions/assume", "chat"])
def test_action_or_message_without_name_is_refused(
    client: TestClient, repository: InMemoryIncidentRepository, stub: WarRoomStub, path: str
) -> None:
    incident = add_incident(repository)

    response = client.post(f"/incidents/{incident.id}/{path}", data={"texto": "oi"})

    assert response.status_code == 401
    assert "informe seu nome" in response.text
    assert response.headers["HX-Retarget"].endswith("-error")
    assert stub.actions == []
    assert stub.questions == []


def test_assume_returns_the_updated_header_and_actions(
    ana: TestClient, repository: InMemoryIncidentRepository, stub: WarRoomStub
) -> None:
    incident = add_incident(repository, state=IncidentState.DETECTED)
    ana.cookies.set(AUTHOR_COOKIE, "Ana")

    response = ana.post(f"/incidents/{incident.id}/actions/assume")

    assert response.status_code == 200
    fragment = response.text
    assert '<section id="war-room">' in fragment
    assert "<html" not in fragment
    assert "Em tratamento" in fragment
    assert '<span class="avatar" aria-hidden="true">A</span>Ana</dd>' in fragment
    assert '<dd class="tta">12 min</dd>' in fragment
    assert "/actions/assume" not in fragment  # já assumido
    assert "/actions/mitigate" in fragment
    assert stub.actions == [(AssumeCommander(), "Ana")]


@pytest.mark.parametrize(
    "case",
    [
        ("severity", {"severidade": "P3"}, ChangeSeverity(Severity.P3)),
        (
            "record",
            {"texto": " fiz rollback do build 20261025.3 "},
            RecordAction("fiz rollback do build 20261025.3"),
        ),
        (
            "discard-hypothesis",
            {"titulo": "Adquirente lenta", "motivo": "latência normal"},
            DiscardHypothesis("Adquirente lenta", "latência normal"),
        ),
        ("mitigate", {}, MarkMitigated()),
        ("reopen", {}, Reopen()),
        ("resolve", {"motivo": "rollback resolveu"}, Resolve("rollback resolveu")),
        ("discard", {"motivo": "teste de carga"}, DiscardIncident("teste de carga")),
    ],
)
def test_each_action_reads_its_form_fields(
    ana: TestClient,
    repository: InMemoryIncidentRepository,
    stub: WarRoomStub,
    case: tuple[str, dict[str, str], HumanAction],
) -> None:
    name, form, expected = case
    # Reabrir só vale para mitigado; as demais ações valem em tratamento.
    state = IncidentState.MITIGATED if name == "reopen" else IncidentState.HANDLING
    incident = add_incident(repository, state=state)

    response = ana.post(f"/incidents/{incident.id}/actions/{name}", data=form)

    assert response.status_code == 200
    assert stub.actions == [(expected, "Ana")]


@pytest.mark.parametrize("name", ["resolve", "discard"])
def test_closing_without_reason_is_refused_with_the_message(
    ana: TestClient, repository: InMemoryIncidentRepository, name: str
) -> None:
    incident = add_incident(repository)

    response = ana.post(f"/incidents/{incident.id}/actions/{name}", data={"motivo": "  "})

    assert response.status_code == 422
    assert "motivo" in response.text
    assert "é obrigatório" in response.text
    assert response.headers["HX-Retarget"] == "#war-room-error"
    assert response.headers["HX-Reswap"] == "innerHTML"


def test_unknown_severity_is_refused(
    ana: TestClient, repository: InMemoryIncidentRepository, stub: WarRoomStub
) -> None:
    incident = add_incident(repository)

    response = ana.post(f"/incidents/{incident.id}/actions/severity", data={"severidade": "P9"})

    assert response.status_code == 422
    assert "severidade inválida" in response.text
    assert stub.actions == []


def test_error_message_is_escaped(
    ana: TestClient, repository: InMemoryIncidentRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident = add_incident(repository)

    async def refuse(**_: object) -> Incident:
        raise InvalidActionError("<b>não</b>")

    monkeypatch.setattr(grolla_api.app, "act", refuse)

    response = ana.post(f"/incidents/{incident.id}/actions/assume")

    assert "&lt;b&gt;não&lt;/b&gt;" in response.text


def test_unknown_action_or_incident_is_not_found(
    ana: TestClient, repository: InMemoryIncidentRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident = add_incident(repository)

    assert ana.post(f"/incidents/{incident.id}/actions/explodir").status_code == 404
    assert ana.post("/incidents/nao-e-uuid/actions/assume").status_code == 404

    async def vanished(**_: object) -> Incident:
        raise IncidentNotFoundError("sumiu")

    monkeypatch.setattr(grolla_api.app, "act", vanished)
    assert ana.post(f"/incidents/{incident.id}/actions/assume").status_code == 404


@pytest.mark.parametrize(
    ("state", "shown", "hidden"),
    [
        (IncidentState.INFORMED, ["assume", "resolve", "discard"], ["mitigate", "reopen"]),
        (IncidentState.HANDLING, ["assume", "mitigate", "severity", "record"], ["reopen"]),
        (IncidentState.MITIGATED, ["assume", "reopen", "resolve"], ["mitigate"]),
        (IncidentState.RESOLVED, [], ["assume", "severity", "record", "resolve", "discard"]),
        (IncidentState.DISCARDED, [], ["assume", "severity", "resolve"]),
    ],
)
def test_only_the_actions_allowed_in_the_state_are_shown(
    ana: TestClient,
    repository: InMemoryIncidentRepository,
    state: IncidentState,
    shown: list[str],
    hidden: list[str],
) -> None:
    incident = add_incident(repository, state=state)

    page = ana.get(f"/incidents/{incident.id}").text

    for name in shown:
        assert f'/actions/{name}"' in page
    for name in hidden:
        assert f'/actions/{name}"' not in page


def test_each_hypothesis_has_a_discard_button(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository)
    repository.analyses[incident.id] = Analysis(
        incident_id=incident.id,
        status=AnalysisStatus.COMPLETED,
        requested_at=T0,
        started_at=T0,
        finished_at=T0,
        summary="",
        evaluations=(),
        hypotheses=(
            Hypothesis(title="Banco degradado", confidence=0.7, evidence=()),
            Hypothesis(title="Adquirente lenta", confidence=0.2, evidence=()),
        ),
        plan=(),
        partial_reason=None,
    )

    page = ana.get(f"/incidents/{incident.id}").text

    assert page.count(f'hx-post="/incidents/{incident.id}/actions/discard-hypothesis"') == 2
    assert 'name="titulo" value="Adquirente lenta"' in page


# ---- cabeçalho


def test_closed_incident_header_shows_tta_ttm_ttr_and_duration_until_closing(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(
        repository,
        state=IncidentState.RESOLVED,
        commander="Ana",
        handling_at=T0 + timedelta(minutes=4),
        mitigated_at=T0 + timedelta(minutes=20),
        resolved_at=T0 + timedelta(minutes=35),
    )

    page = client.get(f"/incidents/{incident.id}").text

    assert '<span class="state state-resolvido">Resolvido</span>' in page
    assert '<span class="sev sev-P1" title="Severidade">P1</span>' in page
    assert '<span class="avatar" aria-hidden="true">A</span>Ana</dd>' in page
    assert '<dd class="tta">4 min</dd>' in page
    assert '<dd class="ttm">20 min</dd>' in page
    assert '<dd class="ttr">35 min</dd>' in page
    assert '<dd class="duration">35 min</dd>' in page


def test_open_incident_header_counts_until_now_and_leaves_times_empty(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository, state=IncidentState.DETECTED)

    page = client.get(f"/incidents/{incident.id}").text

    assert '<span class="state state-detectado">Detectado</span>' in page
    assert '<dd class="duration">12 min</dd>' in page
    assert '<dd class="commander">ninguém ainda</dd>' in page
    assert '<dd class="tta">—</dd>' in page
    assert '<dd class="ttr">—</dd>' in page


def test_list_shows_the_state_in_portuguese(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    add_incident(repository, state=IncidentState.HANDLING)

    assert "Em tratamento" in client.get("/incidents").text


@pytest.mark.parametrize(
    ("delta", "text"),
    [
        (None, ""),
        (timedelta(seconds=-5), "0 min"),
        (timedelta(minutes=4, seconds=59), "4 min"),
        (timedelta(hours=1, minutes=5), "1 h 05 min"),
    ],
)
def test_duration_format(delta: timedelta | None, text: str) -> None:
    assert format_duration(delta) == text


# ---- chat


def agent_reply(incident_id: UUID, question: int) -> ChatMessage:
    return ChatMessage(
        id=0,
        incident_id=incident_id,
        author=AGENT_AUTHOR,
        text="O rollback resolveu: a taxa de erro voltou a 0,2%.",
        at=T0 + timedelta(minutes=2),
        evaluations=(
            Evaluation("query_metric", "taxa_de_erro de pedidos-api", "0,2%", None, ok=True),
        ),
        reply_to=question,
    )


def test_chat_shows_messages_with_agent_highlighted_and_queries_collapsible(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository)
    question = repository.add_message(
        ChatMessage(0, incident.id, "Ana", "o rollback resolveu?", T0 + timedelta(minutes=1)),
        wants_reply=False,
    )
    repository.add_message(agent_reply(incident.id, question.id), wants_reply=False)

    page = client.get(f"/incidents/{incident.id}").text

    assert page.index("o rollback resolveu?") < page.index("O rollback resolveu: a taxa")
    assert 'class="message message-agent"' in page
    assert "<summary>Consultei 1 fonte</summary>" in page
    assert "taxa_de_erro de pedidos-api" in page
    assert "Grolla está investigando" not in page


@pytest.mark.parametrize("status", [ReplyStatus.PENDING, ReplyStatus.RUNNING])
def test_pending_question_shows_the_agent_thinking(
    client: TestClient, repository: InMemoryIncidentRepository, status: ReplyStatus
) -> None:
    incident = add_incident(repository)
    message = ChatMessage(0, incident.id, "Ana", "olha os logs", T0, reply_status=status)
    repository.add_message(message, wants_reply=False)

    fragment = client.get(f"/incidents/{incident.id}/chat").text

    assert "olha os logs" in fragment
    assert "Grolla está investigando" in fragment
    assert "<html" not in fragment


def test_chat_polling_only_swaps_when_something_changed(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository)
    question = repository.add_message(
        ChatMessage(0, incident.id, "Ana", "o banco voltou?", T0), wants_reply=True
    )
    url = f"/incidents/{incident.id}/chat"

    first = client.get(url)
    assert first.status_code == 200
    assert 'hx-trigger="every 3s"' in first.text
    assert f'hx-get="{url}?visto=1-1"' in first.text
    assert client.get(url, params={"visto": "1-1"}).status_code == 204

    repository.messages[0] = replace(question, reply_status=ReplyStatus.ANSWERED)
    repository.add_message(agent_reply(incident.id, question.id), wants_reply=False)

    changed = client.get(url, params={"visto": "1-1"})
    assert changed.status_code == 200
    assert "O rollback resolveu" in changed.text
    assert "visto=2-0" in changed.text


def test_sending_a_message_returns_the_chat_with_it_pending(
    ana: TestClient, repository: InMemoryIncidentRepository, stub: WarRoomStub
) -> None:
    incident = add_incident(repository)

    response = ana.post(f"/incidents/{incident.id}/chat", data={"texto": " o rollback resolveu? "})

    assert response.status_code == 200
    assert '<div id="chat-messages"' in response.text
    assert "<html" not in response.text
    assert "o rollback resolveu?" in response.text
    assert "Grolla está investigando" in response.text
    assert stub.questions == [("Ana", "o rollback resolveu?")]


def test_empty_message_is_refused(ana: TestClient, repository: InMemoryIncidentRepository) -> None:
    incident = add_incident(repository)

    response = ana.post(f"/incidents/{incident.id}/chat", data={"texto": ""})

    assert response.status_code == 422
    assert "escreva a pergunta" in response.text
    assert response.headers["HX-Retarget"] == "#chat-error"


def test_chat_of_unknown_incident_is_not_found(
    ana: TestClient, repository: InMemoryIncidentRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert ana.get("/incidents/nao-e-uuid/chat").status_code == 404
    incident = add_incident(repository)

    async def vanished(*_: object) -> None:
        raise IncidentNotFoundError("sumiu")

    monkeypatch.setattr(grolla_api.app, "ask", vanished)
    assert ana.post(f"/incidents/{incident.id}/chat", data={"texto": "oi"}).status_code == 404


# ---- timeline


def test_timeline_shows_the_author_and_labels_of_war_room_events(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository)
    repository.events = [
        IncidentEvent(incident.id, T0, EventType.COMMANDER_ASSIGNED, {}, author="Ana"),
        IncidentEvent(incident.id, T0, EventType.ACTION_RECORDED, {"texto": "rollback"}, "Ana"),
        IncidentEvent(incident.id, T0, EventType.AGENT_MESSAGE, {}, author=AGENT_AUTHOR),
    ]
    url = f"/incidents/{incident.id}/timeline"

    fragment = client.get(url).text

    assert "IC assumido" in fragment
    assert "Ação registrada" in fragment
    assert "Resposta do agente" in fragment
    assert "por Ana" in fragment
    assert "por Grolla" in fragment
    assert f'hx-get="{url}?visto=3"' in fragment
    assert client.get(url, params={"visto": "3"}).status_code == 204
    assert client.get("/incidents/nao-e-uuid/timeline").status_code == 404


def test_htmx_is_served_as_static(client: TestClient) -> None:
    response = client.get("/static/htmx.min.js")

    assert response.status_code == 200
    assert "htmx" in response.text


# ---- revisão de UX: ciclo, próximo passo, chat com cara de chat


def test_discarded_hypothesis_shows_who_and_why_without_the_button(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository)
    repository.analyses[incident.id] = Analysis(
        incident_id=incident.id,
        status=AnalysisStatus.COMPLETED,
        requested_at=T0,
        started_at=T0,
        finished_at=T0,
        summary="",
        evaluations=(),
        hypotheses=(
            Hypothesis(title="Banco degradado", confidence=0.7, evidence=()),
            Hypothesis(title="Adquirente lenta", confidence=0.2, evidence=()),
        ),
        plan=(),
        partial_reason=None,
    )
    data = {"title": "Adquirente lenta", "reason": "latência normal"}
    repository.events = [
        IncidentEvent(incident.id, T0, EventType.HYPOTHESIS_DISCARDED, data, author="Ana")
    ]

    page = ana.get(f"/incidents/{incident.id}").text

    assert "Descartada por Ana: latência normal" in page
    assert 'class="hypothesis hypothesis-discarded"' in page
    assert page.count(f'hx-post="/incidents/{incident.id}/actions/discard-hypothesis"') == 1


@pytest.mark.parametrize(
    ("state", "current", "primary"),
    [
        (IncidentState.INFORMED, "Detectado", "Assumir como IC"),
        (IncidentState.HANDLING, "Em tratamento", "Marcar mitigado"),
        (IncidentState.MITIGATED, "Mitigado", "Encerrar"),
    ],
)
def test_the_current_stage_is_marked_and_the_next_step_stands_out(
    ana: TestClient,
    repository: InMemoryIncidentRepository,
    state: IncidentState,
    current: str,
    primary: str,
) -> None:
    incident = add_incident(repository, state=state)

    page = ana.get(f"/incidents/{incident.id}").text

    assert f'<li class="atual" aria-current="step">{current}</li>' in page
    assert 'class="button-primary button-big"' in page
    big = page.index('class="button-primary button-big"')
    assert page[big : page.index("</button>", big)].endswith(primary)


def test_resolved_incident_marks_every_stage_done_and_has_no_actions(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository, state=IncidentState.RESOLVED, resolved_at=NOW)

    page = ana.get(f"/incidents/{incident.id}").text

    assert page.count('<li class="feita">') == 4
    assert "/actions/" not in page
    assert "O chat continua aberto" in page


def test_discarded_incident_has_no_cycle(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository, state=IncidentState.DISCARDED, resolved_at=NOW)

    page = ana.get(f"/incidents/{incident.id}").text

    assert 'class="stages"' not in page
    assert "Incidente descartado" in page


def test_my_messages_are_mine_and_the_agent_is_grolla_with_formatted_reply(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository)
    repository.add_message(ChatMessage(0, incident.id, "Ana", "os erros continuam?", T0), False)
    repository.add_message(ChatMessage(0, incident.id, "Bia", "<b>oi</b>", T0), False)
    reply = "- **Taxa:** 0% (http://signoz/x?a=1&b=2).\n- <script>x</script>"
    repository.add_message(ChatMessage(0, incident.id, AGENT_AUTHOR, reply, T0), False)

    fragment = ana.get(f"/incidents/{incident.id}/chat").text

    assert 'class="message message-human message-mine"' in fragment
    assert "<strong>Você</strong>" in fragment
    assert "<strong>Bia</strong>" in fragment
    assert "&lt;b&gt;oi&lt;/b&gt;" in fragment
    assert "<strong>Grolla</strong>" in fragment
    assert "<li><strong>Taxa:</strong> 0% (" in fragment
    assert '<a href="http://signoz/x?a=1&amp;b=2" target="_blank" rel="noopener">' in fragment
    assert "&lt;script&gt;" in fragment
    assert "<script>" not in fragment


def test_failed_reply_is_marked(ana: TestClient, repository: InMemoryIncidentRepository) -> None:
    incident = add_incident(repository)
    text = "não consegui responder: LLM indisponível"
    repository.add_message(ChatMessage(0, incident.id, AGENT_AUTHOR, text, T0), False)

    fragment = ana.get(f"/incidents/{incident.id}/chat").text

    assert "message-failed" in fragment


def test_empty_chat_offers_suggestions_that_send_with_one_click(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository)

    fragment = ana.get(f"/incidents/{incident.id}/chat").text

    assert "Oi, eu sou o Grolla" in fragment
    assert '<input type="hidden" name="texto" value="Os erros continuam?">' in fragment
    assert fragment.count(f'hx-post="/incidents/{incident.id}/chat"') == 3


def test_empty_chat_without_name_has_no_suggestions(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository)

    fragment = client.get(f"/incidents/{incident.id}/chat").text

    assert "Oi, eu sou o Grolla" in fragment
    assert 'name="texto"' not in fragment


def test_timeline_reads_well_and_hides_card_queueing(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository)
    repository.events = [
        IncidentEvent(
            incident.id, T0, EventType.STATE_CHANGED, {"from": "detectado", "to": "em_tratamento"}
        ),
        IncidentEvent(incident.id, T0, EventType.NOTIFICATION_QUEUED, {"kind": "x"}),
        IncidentEvent(incident.id, T0, EventType.NOTIFICATION_DELIVERED, {"kind": "x"}),
        IncidentEvent(
            incident.id, T0, EventType.AGENT_MESSAGE, {"text": "**Taxa:** 0%"}, author=AGENT_AUTHOR
        ),
    ]

    fragment = client.get(f"/incidents/{incident.id}/timeline").text

    assert "Detectado → Em tratamento" in fragment
    assert "Notificação enfileirada" not in fragment
    assert '<li class="quiet" data-kind="sistema">' in fragment
    assert "Taxa: 0%" in fragment
    assert "**" not in fragment
    assert "visto=4" in fragment  # a versão conta todos os eventos, mesmo os escondidos


def test_incident_list_shows_how_many_are_open(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    add_incident(repository, state=IncidentState.HANDLING, commander="Ana")
    add_incident(repository, state=IncidentState.RESOLVED)

    page = client.get("/incidents").text

    assert "1 aberto" in page
    assert "2 de 2 incidentes" in page
    assert "IC Ana" in page
    assert page.index("Precisa de você agora") < page.index('id="encerrados"')


@pytest.mark.parametrize(
    ("minutes", "text"),
    [(0, "agora"), (5, "há 5 min"), (65, "há 1 h 05 min"), (3 * 24 * 60, "há 3 dias")],
)
def test_ago(minutes: int, text: str) -> None:
    assert ago(timedelta(minutes=minutes)) == text


def test_rich_text_splits_paragraphs_and_lists() -> None:
    html = str(rich_text("Primeiro.\nmesmo parágrafo\n\n- a\n- `b`\ndepois"))

    assert html == (
        "<p>Primeiro.<br>mesmo parágrafo</p><ul><li>a</li><li><code>b</code></li></ul><p>depois</p>"
    )


def test_initials() -> None:
    assert initials("Ana Maria Souza") == "AM"
    assert initials("  ") == "?"


def test_rich_text_turns_markdown_links_into_links() -> None:
    html = str(rich_text("veja o [build 5](https://ado/b?id=5&x=1) e http://signoz/t."))

    assert html == (
        '<p>veja o <a href="https://ado/b?id=5&amp;x=1" target="_blank" rel="noopener">build 5</a>'
        ' e <a href="http://signoz/t" target="_blank" rel="noopener">http://signoz/t</a>.</p>'
    )


def test_rich_text_never_links_other_schemes() -> None:
    html = str(rich_text("[x](javascript:alert(1)) e javascript:alert(1)"))

    assert "<a" not in html

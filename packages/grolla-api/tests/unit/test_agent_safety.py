"""O cerco do agente na api: selo de comando destrutivo, recusa no chat e no console, limite de
mensagens por pessoa e os pedidos recusados em /metricas.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import grolla_api.app
import pytest
from fastapi.testclient import TestClient
from grolla.application.safety import MessageCounter, RateLimitedError
from grolla.domain.analysis import Action, Analysis, AnalysisStatus, Verification
from grolla.domain.chat import AGENT_AUTHOR, ChatMessage
from grolla.domain.console import ConsoleMessage
from grolla.domain.incidents import IncidentState
from grolla.domain.metrics import Period, PeriodMetrics
from grolla.domain.safety import REFUSALS, Scope
from grolla.testing.builders import make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.console import InMemoryConsoleStore
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla.testing.messages import FakeMessageCounter
from grolla_api.app import Services, create_app
from grolla_api.metrics_pages import is_empty
from grolla_api.war_room import AUTHOR_COOKIE, rich_text, situation_of

NOW = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
BADGE = "destrutivo, confira antes"
DELETE_POD = "kubectl delete pod estoque-api-7d4f -n loja"


@pytest.fixture
def repository() -> InMemoryIncidentRepository:
    return InMemoryIncidentRepository()


@pytest.fixture
def console() -> InMemoryConsoleStore:
    return InMemoryConsoleStore()


@pytest.fixture
def counter() -> FakeMessageCounter:
    return FakeMessageCounter()


@pytest.fixture
def ana(
    services: Services,
    repository: InMemoryIncidentRepository,
    console: InMemoryConsoleStore,
    counter: FakeMessageCounter,
) -> TestClient:
    app = create_app(
        replace(
            services,
            repository=repository,
            console=console,
            messages=counter,
            clock=FakeClock(NOW),
        )
    )
    client = TestClient(app, follow_redirects=False)
    client.cookies.set(AUTHOR_COOKIE, "Ana")
    return client


def verification(*actions: str) -> Verification:
    return Verification(
        incident_id=uuid4(),
        status=AnalysisStatus.COMPLETED,
        requested_at=NOW,
        started_at=NOW,
        finished_at=NOW,
        verdicts=(),
        root_cause="Pod da estoque-api preso em CrashLoopBackOff.",
        actions=tuple(Action(action, "estoque-api", "O pod volta", None) for action in actions),
        missing="",
        evaluations=(),
        partial_reason=None,
    )


# ---- selo de comando destrutivo


def test_step_with_a_destructive_command_is_flagged() -> None:
    s = situation_of(None, verification(f"Apagar o pod: `{DELETE_POD}`", "Ver `kubectl get pods`"))

    assert s.steps[0].command == DELETE_POD
    assert s.steps[0].destructive
    assert not s.steps[1].destructive


def test_plan_of_the_analysis_flags_the_destructive_command() -> None:
    analysis = Analysis(
        incident_id=uuid4(),
        status=AnalysisStatus.COMPLETED,
        requested_at=NOW,
        started_at=NOW,
        finished_at=NOW,
        summary="Pod preso.",
        evaluations=(),
        hypotheses=(),
        plan=("Zerar o deployment: `kubectl scale deploy/estoque-api --replicas=0`", "Ver logs"),
        partial_reason=None,
    )

    s = situation_of(analysis, None)

    assert [step.destructive for step in s.steps] == [True, False]


def test_now_card_shows_the_badge_and_copy_asks_for_confirmation(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = make_incident(state=IncidentState.HANDLING)
    repository.incidents = {incident.id: incident}
    repository.verifications = {
        incident.id: replace(
            verification(f"`{DELETE_POD}`", "Depois: `kubectl delete deploy estoque-api`"),
            incident_id=incident.id,
        )
    }

    page = ana.get(f"/incidents/{incident.id}").text
    first = page.split('<div class="next-move')[1].split('class="more-steps"')[0]
    later = page.split('class="more-steps"')[1].split("</details>")[0]

    assert BADGE in first
    assert f'data-copy="{DELETE_POD}" data-confirm="' in first
    assert 'class="command command-destructive"' in first
    assert BADGE in later
    assert "O Grolla não executa nada" in page


def test_safe_command_copies_without_confirmation(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = make_incident(state=IncidentState.HANDLING)
    repository.incidents = {incident.id: incident}
    repository.verifications = {
        incident.id: replace(verification("`kubectl get pods -n loja`"), incident_id=incident.id)
    }

    page = ana.get(f"/incidents/{incident.id}").text

    assert 'data-copy="kubectl get pods -n loja">' in page
    assert BADGE not in page
    assert "data-confirm" not in page


def test_copy_button_script_confirms_before_copying(ana: TestClient) -> None:
    script = ana.get("/static/war-room.js").text

    assert "copy.dataset.confirm && !window.confirm(copy.dataset.confirm)" in script


def test_code_in_the_messages_gets_the_badge_only_when_destructive() -> None:
    html = rich_text(f"Para voltar: `{DELETE_POD}`. Antes, `kubectl get pods`.")

    assert html.count(BADGE) == 1
    assert f"<code>{DELETE_POD}</code> <span" in html
    assert "<code>kubectl get pods</code>." in html


async def answer(console: InMemoryConsoleStore, text: str, refusal: Scope | None = None) -> None:
    """O worker responde a pergunta pendente da conversa."""
    job = await console.claim(NOW)
    assert job is not None
    reply = ConsoleMessage(
        0, job.session_id, AGENT_AUTHOR, text, NOW, reply_to=job.message_id, refusal=refusal
    )
    await console.answer(job, reply, [], NOW)


async def test_chat_and_console_messages_flag_destructive_code(
    ana: TestClient, repository: InMemoryIncidentRepository, console: InMemoryConsoleStore
) -> None:
    incident = make_incident(state=IncidentState.HANDLING)
    repository.incidents = {incident.id: incident}
    text = f"Se precisar, o time roda `{DELETE_POD}`."
    repository.add_message(ChatMessage(0, incident.id, AGENT_AUTHOR, text, NOW), False)
    session_id = new_session(ana)
    await answer(console, text)

    assert BADGE in ana.get(f"/incidents/{incident.id}/chat").text
    assert BADGE in ana.get(f"/console/{session_id}/mensagens").text


# ---- recusa no chat e no console


def new_session(ana: TestClient) -> UUID:
    response = ana.post("/console", data={"texto": "o autorizador está saudável?"})
    assert response.status_code == 303
    return UUID(response.headers["location"].removeprefix("/console/"))


def test_refusal_in_the_chat_has_its_own_style(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = make_incident(state=IncidentState.HANDLING)
    repository.incidents = {incident.id: incident}
    refusal = ChatMessage(
        0, incident.id, AGENT_AUTHOR, REFUSALS[Scope.EXECUTION], NOW, refusal=Scope.EXECUTION
    )
    repository.add_message(refusal, False)

    fragment = ana.get(f"/incidents/{incident.id}/chat").text

    assert "message-refused" in fragment
    assert '<p class="refusal"><svg' in fragment
    assert "Não executo nada no ambiente" in fragment


async def test_refusal_in_the_console_has_its_own_style(
    ana: TestClient, console: InMemoryConsoleStore
) -> None:
    session_id = new_session(ana)
    await answer(console, REFUSALS[Scope.OFF_TOPIC], Scope.OFF_TOPIC)

    fragment = ana.get(f"/console/{session_id}/mensagens").text

    assert "message-refused" in fragment
    assert "Só ajudo com o ambiente, os incidentes e a operação." in fragment


def test_normal_answer_is_not_a_refusal(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = make_incident(state=IncidentState.HANDLING)
    repository.incidents = {incident.id: incident}
    repository.add_message(ChatMessage(0, incident.id, AGENT_AUTHOR, "Tudo certo.", NOW), False)

    assert "message-refused" not in ana.get(f"/incidents/{incident.id}/chat").text


# ---- limite de mensagens por pessoa


def fill(counter: FakeMessageCounter, author: str, count: int) -> None:
    """`count` mensagens do autor na última hora, a mais antiga 18 minutos atrás."""
    counter.sent += [
        (author, NOW - timedelta(minutes=18) + timedelta(seconds=i)) for i in range(count)
    ]


def test_chat_over_the_limit_is_429_without_recording(
    ana: TestClient, repository: InMemoryIncidentRepository, counter: FakeMessageCounter
) -> None:
    incident = make_incident(state=IncidentState.HANDLING)
    repository.incidents = {incident.id: incident}
    fill(counter, "Ana", 30)

    response = ana.post(f"/incidents/{incident.id}/chat", data={"texto": "e agora?"})

    assert response.status_code == 429
    assert "Limite de 30 mensagens por hora; tente de novo às " in response.text
    assert response.headers["HX-Retarget"] == "#chat-error"
    assert repository.messages == []


async def test_new_console_conversation_over_the_limit_is_429(
    ana: TestClient, console: InMemoryConsoleStore, counter: FakeMessageCounter
) -> None:
    fill(counter, "Ana", 30)

    response = ana.post("/console", data={"texto": "o autorizador está saudável?"})

    assert response.status_code == 429
    assert "Limite de 30 mensagens por hora" in response.text
    assert response.headers["HX-Retarget"] == "#console-error"
    assert await console.sessions(10) == []


async def test_console_question_over_the_limit_is_429(
    ana: TestClient, console: InMemoryConsoleStore, counter: FakeMessageCounter
) -> None:
    session_id = new_session(ana)
    fill(counter, "Ana", 30)

    response = ana.post(f"/console/{session_id}", data={"texto": "e o p99?"})

    assert response.status_code == 429
    assert "Limite de 30 mensagens por hora" in response.text
    assert len(await console.messages(session_id)) == 1  # só a pergunta que abriu a conversa


def test_limit_is_per_author(
    ana: TestClient, repository: InMemoryIncidentRepository, counter: FakeMessageCounter
) -> None:
    incident = make_incident(state=IncidentState.HANDLING)
    repository.incidents = {incident.id: incident}
    fill(counter, "Bruno", 30)
    fill(counter, "Ana", 29)

    response = ana.post(f"/incidents/{incident.id}/chat", data={"texto": "e agora?"})

    assert response.status_code == 200


def test_limit_warning_says_when_to_write_again(
    ana: TestClient,
    repository: InMemoryIncidentRepository,
    counter: FakeMessageCounter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incident = make_incident(state=IncidentState.HANDLING)
    repository.incidents = {incident.id: incident}
    asked: list[tuple[MessageCounter, str, datetime, int]] = []

    async def limited(given: MessageCounter, author: str, now: datetime, per_hour: int) -> None:
        asked.append((given, author, now, per_hour))
        raise RateLimitedError(per_hour, datetime(2026, 10, 27, 15, 42, tzinfo=UTC))

    monkeypatch.setattr(grolla_api.app, "ensure_under_limit", limited)

    response = ana.post(f"/incidents/{incident.id}/chat", data={"texto": "e agora?"})

    assert "Limite de 30 mensagens por hora; tente de novo às 15:42" in response.text
    assert asked == [(counter, "Ana", NOW, 30)]


# ---- pedidos recusados em /metricas


def period_row(label: str, refused: int) -> PeriodMetrics:
    return PeriodMetrics(
        Period(NOW.date(), NOW.date(), label), 0, 0, 0, 0, None, 0, 0, 0, refused=refused
    )


def test_metrics_show_the_refused_requests(
    ana: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def rows(*_: object) -> tuple[PeriodMetrics, ...]:
        return (period_row("out/2026", 3), period_row("set/2026", 0))

    monkeypatch.setattr(grolla_api.app, "agent_metrics", rows)

    page = ana.get("/metricas").text

    assert '<th scope="col">Pedidos recusados</th>' in page
    assert "<dt>Pedidos recusados</dt><dd>3</dd>" in page
    row = page.split('<th scope="row">out/2026</th>')[1].split("</tr>")[0]
    assert row.rstrip().endswith("<td>3</td>")


def test_period_with_only_refusals_is_not_empty() -> None:
    assert not is_empty(period_row("out/2026", 1))
    assert is_empty(period_row("out/2026", 0))
